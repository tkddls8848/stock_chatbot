"""사전선별의 조립과 파이프라인 연결 검증.

단위 동작(점수·사건 군집·학습)은 `test_news_prefilter.py`가 본다. 여기서는
그 점수가 **실제로 큐에 담기는 기사를 바꾸는지**, 바꾸지 못할 때 뉴스가 그대로
나가는지, 라벨을 이을 식별자가 따라가는지, 관리 명령이 그 결과를 여는지를 본다.

연결은 `collect_report_source`를 통해 본다 — 스케줄러가 매시간 부르는 경로다.
사전선별이 바꾸는 것은 **큐에 담기는 기사**이지 번역 대상이 아니다: 예약 경로는
기사별 번역을 하지 않는다.

연결은 `prepare_global_source`를 통해 본다. 이 래퍼 자체는 스케줄러가 부르지
않지만(`test_news_digest.py` 참고), 사전선별 순서를 실제로 정하는 자리는 그
안쪽의 `collect_source_candidates`이고 예약 보고서 경로도 같은 함수를 지난다.
래퍼를 바꿀 때 이 파일이 무엇을 덮는지 착각하지 않도록 적어 둔다.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from shared.core.clock import now
from telegram_bot.features.news_prefilter import feature as prefilter_feature
from telegram_bot.features.news_prefilter.service import RankedCandidate
from telegram_bot.features.system_admin import handlers as admin
from telegram_bot.news.report import collect_report_source
from telegram_bot.news.registry import SourceSpec
from telegram_bot.news.sources import GlobalArticle


# ── 조립 ──────────────────────────────────────────────

class _Scheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func, **kwargs):
        self.jobs.append({"func": func, **kwargs})


class _StockDb:
    def get_candidate_universe(self):
        return [
            {
                "code": "US:NASDAQ:AAPL",
                "display_name": "Apple Inc",
                "cn_name": "苹果公司",
                "ko_name": "애플",
                "market": "US",
            }
        ]


@pytest.fixture
def installed(tmp_path, monkeypatch):
    for name, filename in (
        ("NEWS_PREFILTER_EVENT_FILE", "event_memory.json"),
        ("NEWS_PREFILTER_OBSERVATION_FILE", "observations.jsonl"),
        ("NEWS_PREFILTER_MODEL_FILE", "model.json"),
        ("NEWS_PREFILTER_CPU_STATE_FILE", "cpu_budget.json"),
    ):
        monkeypatch.setattr(prefilter_feature, name, tmp_path / filename)
    app = SimpleNamespace(bot_data={"stock_db": _StockDb()})
    prefilter_feature._install_services(app)
    scheduler = _Scheduler()
    prefilter_feature._install_jobs(scheduler, app)
    return app, scheduler


def test_service_is_installed_under_the_name_the_pipeline_looks_up(installed):
    """파이프라인은 bot_data['news_prefilter']로만 찾는다.

    이름이 어긋나면 예외 없이 조용히 최신순으로 돌아가므로 여기서 고정한다.
    """
    app, _ = installed

    assert "news_prefilter" in app.bot_data
    assert app.bot_data["news_prefilter"].mode == "shadow"


def test_maintenance_job_starts_late_and_never_overlaps(installed):
    """기동 직후는 뉴스·종목 DB가 붐비므로 보정을 바로 얹지 않는다."""
    _, scheduler = installed

    assert len(scheduler.jobs) == 1
    job = scheduler.jobs[0]
    assert job["id"] == "news_prefilter_maintenance"
    assert job["trigger"] == "interval"
    assert job["max_instances"] == 1
    assert job["coalesce"] is True
    assert job["next_run_time"] >= now() + timedelta(minutes=4)


# ── 파이프라인 연결 ─────────────────────────────────────

class _Queue:
    """받은 항목을 그대로 받아 주고 순서를 기록하는 최소 큐 대역."""

    def __init__(self):
        self.items = []

    async def enqueue(self, items):
        self.items.extend(items)
        return items


class _Tracker:
    def __init__(self):
        self.confirmed = []

    async def reserve(self, article_id):
        return True

    async def release(self, article_id):
        return None

    async def confirm(self, article_id):
        self.confirmed.append(article_id)


class _Registry:
    def record_success(self, key):
        return None

    def record_failure(self, key, reason):
        return None


class _Prefilter:
    """순서를 뒤집기만 하는 최소 대역. 실제 점수는 단위 테스트가 본다."""

    def __init__(self, fail=False):
        self.fail = fail
        self.outcomes = []
        self.cycle_ids = []

    async def rank_articles(self, *, source, market, articles, watchlist, cycle_id):
        self.cycle_ids.append(cycle_id)
        if self.fail:
            raise RuntimeError("사건 메모리 손상")
        return [
            RankedCandidate(
                article=article,
                candidate_id=f"cand-{article.article_id}",
                event_id="e1",
                score=float(index),
                features={},
                prefilter_rank=index,
            )
            for index, article in enumerate(reversed(articles))
        ]

    async def record_outcome(self, *, candidate_id, impact, sentiment):
        self.outcomes.append((candidate_id, impact, sentiment))


def _articles(count):
    stamp = now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        GlobalArticle(
            article_id=f"article-{index}",
            title=f"기사 {index}",
            content="본문",
            published_at=stamp,
        )
        for index in range(count)
    ]


def _collect(prefilter, count=4):
    """스케줄러가 부르는 수집 한 주기를 소스 하나에 대해 돌린다."""
    spec = SourceSpec(
        key="gnews_us", label="US", fetch=lambda: _articles(count), market="US"
    )
    queue = _Queue()
    accepted = asyncio.run(
        collect_report_source(
            spec, _Registry(), _Tracker(), queue, {}, prefilter, "cycle-1"
        )
    )
    return queue, accepted


def _titles(queue):
    return [item["title"] for item in queue.items]


def test_prefilter_order_decides_which_articles_are_queued(monkeypatch):
    """기능의 존재 이유. 큐는 피드 순서가 아니라 사전선별 순서를 따라야 한다."""
    monkeypatch.setattr("telegram_bot.news.report.NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT", 2)

    queue, accepted = _collect(_Prefilter())

    # 대역이 순서를 뒤집었으므로 마지막 기사부터 담긴다.
    assert _titles(queue) == ["기사 3", "기사 2"]
    assert accepted == 2


def test_queued_count_is_unchanged_by_the_prefilter(monkeypatch):
    """추가 Neurons가 0이라는 전제. 순서만 바뀌고 건수는 그대로다."""
    monkeypatch.setattr("telegram_bot.news.report.NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT", 2)

    with_prefilter, _ = _collect(_Prefilter())
    without, _ = _collect(None)

    assert len(with_prefilter.items) == len(without.items) == 2


def test_a_broken_prefilter_falls_back_to_recency_instead_of_dropping_news(monkeypatch):
    """로컬 보조 기능의 실패가 뉴스를 멈추게 해서는 안 된다."""
    monkeypatch.setattr("telegram_bot.news.report.NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT", 2)

    queue, accepted = _collect(_Prefilter(fail=True))

    assert _titles(queue) == ["기사 0", "기사 1"]
    assert accepted == 2
    # 라벨을 이을 수 없으므로 candidate_id는 비운다.
    assert all(item["prefilter_candidate_id"] == "" for item in queue.items)


def test_candidate_id_rides_along_so_the_label_can_be_joined(monkeypatch):
    """큐 항목이 후보 식별자를 들고 가야 나중에 라벨을 이어 붙일 수 있다.

    지금 그 라벨을 되먹이는 호출자는 없다(CLAUDE.md의 라벨 공급 항목). 식별자가
    큐까지 살아 오는 것만이라도 고정해 두면, 공급자를 붙일 때 저장 형식을 다시
    설계하지 않아도 된다.
    """
    monkeypatch.setattr("telegram_bot.news.report.NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT", 2)

    queue, _ = _collect(_Prefilter())

    assert [item["prefilter_candidate_id"] for item in queue.items] == [
        f"cand-{item['article_id']}" for item in queue.items
    ]
    assert all(item["prefilter_candidate_id"] for item in queue.items)


# ── /system prefilter ─────────────────────────────────

class _Message:
    def __init__(self):
        self.texts = []
        self.markups = []

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)
        self.markups.append(kwargs.get("reply_markup"))


def _run_system(bot_data, args):
    message = _Message()
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(args=args, bot_data=bot_data)
    asyncio.run(admin.cmd_system(update, context))
    return message


class _ReportingPrefilter:
    def __init__(self, **overrides):
        self.payload = {
            "mode": "shadow",
            "cycles": 12,
            "candidates_seen": 3000,
            "logged": 156,
            "new_event_ratio": 0.42,
            "events": 480,
            "agree": 40,
            "latest_only": 8,
            "prefilter_only": 8,
            "labeled": 60,
            "positives": 21,
            "auc": 0.63,
            "model_trained_at": "",
            "model_validation_ap": None,
            "model_label_count": None,
            "model_prevalence": None,
            "cpu": {
                "utc_day": "2026-08-17",
                "budget_seconds": 12960,
                "used_seconds": 3240,
                "remaining_seconds": 9720,
                "foreground_seconds": 1800,
            },
        }
        self.payload.update(overrides)

    async def report(self):
        return self.payload


def test_system_prefilter_says_so_when_the_feature_is_off():
    text = _run_system({}, ["prefilter"]).texts[-1]

    assert "news_prefilter" in text


def test_system_prefilter_shows_disagreement_and_discrimination():
    text = _run_system({"news_prefilter": _ReportingPrefilter()}, ["prefilter"]).texts[-1]

    assert "shadow" in text
    assert "0.630" in text  # AUC
    assert "최신순만 8건" in text
    assert "0.90h / 3.60h" in text  # CPU 예산(보정)
    assert "0.50h" in text  # foreground는 참고용으로만 표시


def test_system_prefilter_always_carries_the_shadow_caveat():
    """이 한계를 지운 채로 active에 올리지 않는다는 약속을 화면에서 고정한다."""
    text = _run_system({"news_prefilter": _ReportingPrefilter()}, ["prefilter"]).texts[-1]

    assert "섀도가 답하지 못하는 것" in text
    assert "탐색 슬롯" in text


def test_agreeing_policies_are_called_out_as_no_reason_to_switch():
    """불일치가 0이면 바꿔도 같은 기사다. 그 사실을 읽는 사람에게 알려야 한다."""
    prefilter = _ReportingPrefilter(latest_only=0, prefilter_only=0)

    text = _run_system({"news_prefilter": prefilter}, ["prefilter"]).texts[-1]

    assert "바꿀 이유가 아직 없습니다" in text


def test_untrained_model_is_reported_instead_of_shown_as_zero():
    text = _run_system({"news_prefilter": _ReportingPrefilter()}, ["prefilter"]).texts[-1]

    assert "아직 학습된 모델이 없습니다" in text


def test_unknown_subcommand_lists_prefilter_too():
    text = _run_system({}, ["없는항목"]).texts[-1]

    assert "prefilter" in text


def test_system_status_carries_a_prefilter_button():
    message = _run_system({}, [])

    buttons = [
        button for row in message.markups[-1].inline_keyboard for button in row
    ]
    assert "nav:system:prefilter" in {button.callback_data for button in buttons}


def test_menu_button_routes_to_the_prefilter_report(monkeypatch):
    from telegram_bot.handlers import navigation

    seen = {}

    async def fake_cmd_system(update, context):
        seen["args"] = context.args

    monkeypatch.setattr("telegram_bot.features.system_admin.handlers.cmd_system", fake_cmd_system)
    update = SimpleNamespace(callback_query=SimpleNamespace(message=_Message()))
    context = SimpleNamespace(
        args=[],
        bot_data={"feature_registry": SimpleNamespace(menu_owner=lambda _data: None)},
        user_data={},
        application=None,
    )

    handled = asyncio.run(
        navigation.handle_menu_callback(update, context, "nav:system:prefilter")
    )

    assert handled is True
    assert seen["args"] == ["prefilter"]
