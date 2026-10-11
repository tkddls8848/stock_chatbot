"""공개 웹이 읽을 마지막 산출물을 공유 저장소(`storage/public/`)에 쓴다.

봇 코드다. 예전에는 웹 패키지(`services/web/export.py`)에 있어 봇이 웹을 지연
import했는데, 공유 저장소로 옮기며 그 유일한 교차 import를 없앴다 — 봇은 자기
`core/storage.py`로 쓰고, 웹은 같은 파일을 자기 코드로 읽기만 한다. 경로와
JSON 형식이 둘 사이의 계약이고, 형식을 바꾸면 웹의 읽는 쪽 테스트를 함께 고친다.
공개 웹 요청이 LLM, matplotlib, 혹은 봇의 상태를 실행시키지 않는 것은 그대로다.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any
from datetime import date, datetime, timedelta

from services.telegram_bot.core.clock import now
from services.telegram_bot.core.config import DATA_DIR, PUBLIC_DIR, WATCH_NEWS_HOURS, WATCH_NEWS_MAX_ITEMS
from services.telegram_bot.core.storage import write_bytes_atomic, write_json_atomic
from services.telegram_bot.news.utils import junk_reason, parse_news_datetime

logger = logging.getLogger(__name__)

MARKET_JSON = PUBLIC_DIR / "market.json"
MARKET_CHART = PUBLIC_DIR / "market_chart.png"
# 운영자 봇의 리서치는 개인 기록이다. 공개 저장소에 새 복사본을 만들지 않는다.
RESEARCH_JSON = DATA_DIR / "research" / "snapshot.json"
NEWS_JSON = PUBLIC_DIR / "news.json"
# 웹 개인 리서치의 입력 묶음. 운영자 리서치와 같은 수집기·후보 구성을 관심종목 없이 돌린 결과다.
# 공개 라우트가 내보내지 않는다 — 웹은 로그인한 계정의 리서치 실행에서만 읽는다.
RESEARCH_INPUTS_JSON = PUBLIC_DIR / "research_inputs.json"
# 입력 묶음 형식. 바꾸면 웹의 읽는 쪽(`services/web/personal_research.py`)을 같은 커밋에서 고친다.
RESEARCH_INPUTS_FORMAT = 1
# 웹 "내 자산 점검"이 보유 종목을 찾는 기사 묶음. 보고서 큐에 들어온 원문 제목을 최근 72시간 굴려 둔다.
# 공개 라우트가 내보내지 않는다 — 웹은 로그인한 계정의 점검과 그 계정이 켠 뉴스레터에서만 읽는다.
WATCH_NEWS_JSON = PUBLIC_DIR / "watch_news.json"
# 형식. 바꾸면 웹의 읽는 쪽(`services/web/portfolio/watch.py`)을 같은 커밋에서 고친다.
WATCH_NEWS_FORMAT = 1
META_JSON = PUBLIC_DIR / "meta.json"
_META_LOCK = threading.Lock()
_NEWS_LOCK = threading.Lock()
_WATCH_LOCK = threading.Lock()


def publish_news(documents: list[dict[str, Any]]) -> None:
    """발행된 보고서·근거 제목만 공개한다. 최근 30일, 최대 3,000건을 보존한다.

    원문 전체·관심종목·리서치 상태는 받지 않는다. 검색 요청은 이 산출물만 읽는다.
    근거 기사는 원문 제목(`text`)이 기사가 아닌 글(`news/utils.py`의 `junk_reason`)이면 이미 공개한
    것까지 뺀다 — 규칙이 늘면 그 전에 공개된 광고·시세 화면이 다음 발행 때 함께 빠진다(2026-10-07 공개
    근거 2,795건 중 96건이 그런 글이었고, 규칙이 생기기 전이라 30일 보존 기간 동안 검색에 남아 있었다).
    원문 제목을 남기지 않던 2026-10-01 이전 근거(`text`가 빈 문자열)는 판정할 수 없어 그대로 둔다.
    """
    moment = now()
    cutoff = (moment.date() - timedelta(days=29)).isoformat()
    target = NEWS_JSON
    fields = (
        "id", "kind", "market", "title", "text", "date", "published_at",
        "source", "url", "sentiment",
    )
    with _NEWS_LOCK:
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            existing = {}
        # 손상된 산출물은 덮어써서 성공으로 보고하지 않는다.
        rows = {row["id"]: row for row in existing.get("documents", [])}
        for document in documents:
            day = date.fromisoformat(document["date"]).isoformat()
            if cutoff <= day <= moment.date().isoformat():
                rows[document["id"]] = {key: document[key] for key in fields if key in document}
        kept = sorted(
            (row for row in rows.values() if cutoff <= row["date"] <= moment.date().isoformat()
             and not (row.get("kind") == "news" and junk_reason(row.get("text", ""), row.get("source", "")))),
            key=lambda row: (row["date"], row.get("published_at", ""), row["id"]),
            reverse=True,
        )[:3000]
        generated_at = moment.isoformat(timespec="seconds")
        write_json_atomic(target, {"generated_at": generated_at, "documents": kept})
    _update_meta("news_generated_at", generated_at)


def publish_watch_news(queued: list[dict[str, Any]]) -> int:
    """보고서 큐 항목을 굴리는 기사 묶음에 합친다. 남긴 건수를 돌려준다.

    큐는 보고서가 발행하면 비워지므로, 매시간 수집이 끝날 때마다 그때 큐에 있는 것을 기사 ID로
    합쳐 둔다 — 큐에 한 시간이라도 머문 기사는 다 남는다. 발행 시각을 못 읽은 기사와 기사가 아닌 글
    (`junk_reason`)은 넣지 않는다. 제목·매체·주소·시각만 담고 사전선별 점수·사건 ID는 담지 않는다.
    """
    moment = now()
    cutoff = moment - timedelta(hours=WATCH_NEWS_HOURS)
    with _WATCH_LOCK:
        try:
            existing = json.loads(WATCH_NEWS_JSON.read_text(encoding="utf-8"))
        except FileNotFoundError:
            existing = {}
        # 형식이 다른 묶음은 이어 쓰지 않고 새로 시작한다(현재 형식만 읽는다).
        rows = ({row["id"]: row for row in existing.get("items", [])}
                if existing.get("format") == WATCH_NEWS_FORMAT else {})
        for item in queued:
            published = parse_news_datetime(item.get("published_at"), item.get("published_date"))
            title = str(item.get("title") or "").strip()
            publisher = str(item.get("publisher") or "")
            if published is None or not title or junk_reason(title, publisher):
                continue
            rows[str(item["article_id"])] = {
                "id": str(item["article_id"]), "market": str(item.get("market") or ""), "title": title,
                "source": publisher or str(item.get("label") or ""), "url": str(item.get("url") or ""),
                "published_at": published.isoformat(timespec="seconds"),
            }
        kept = sorted((row for row in rows.values()
                       if cutoff <= datetime.fromisoformat(row["published_at"]) <= moment + timedelta(hours=1)),
                      key=lambda row: (row["published_at"], row["id"]), reverse=True)[:WATCH_NEWS_MAX_ITEMS]
        write_json_atomic(WATCH_NEWS_JSON, {"format": WATCH_NEWS_FORMAT,
                                            "generated_at": moment.isoformat(timespec="seconds"),
                                            "hours": WATCH_NEWS_HOURS, "items": kept})
    return len(kept)


def _update_meta(key: str, generated_at: str) -> None:
    """산출물 시각을 합쳐 쓴다. 동시 갱신이 다른 키를 지우면 안 된다."""
    with _META_LOCK:
        try:
            existing = json.loads(META_JSON.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        existing[key] = generated_at
        existing["updated_at"] = now().isoformat(timespec="seconds")
        write_json_atomic(META_JSON, existing, indent=2)


def publish_market(
    image: bytes,
    markets: dict[str, dict[str, Any]],
    lookback_days: int,
) -> None:
    """렌더된 차트와 그 수치를 함께 저장한다."""
    generated_at = now().isoformat(timespec="seconds")
    write_bytes_atomic(MARKET_CHART, image)
    write_json_atomic(
        MARKET_JSON,
        {
            "generated_at": generated_at,
            "lookback_days": lookback_days,
            "markets": markets,
        },
        indent=2,
    )
    _update_meta("market_generated_at", generated_at)


def publish_research_inputs(
    news_items: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    sector_summary_context: dict[str, Any] | None,
) -> None:
    """웹 개인 리서치가 읽을 입력 묶음. 관심종목·운영자 주제·개인 데이터는 담지 않는다."""
    write_json_atomic(
        RESEARCH_INPUTS_JSON,
        {
            "format": RESEARCH_INPUTS_FORMAT,
            "generated_at": now().isoformat(timespec="seconds"),
            "news_items": news_items,
            "candidates": candidates,
            "sector_summary_context": sector_summary_context,
        },
    )


def publish_research(
    sight: str,
    result: dict[str, Any],
    history: list[dict[str, Any]],
) -> None:
    """완료된 리서치의 전체 결과와 프롬프트용 압축 이력을 분리해 저장한다."""
    generated_at = now().isoformat(timespec="seconds")
    write_json_atomic(
        RESEARCH_JSON,
        {
            "generated_at": generated_at,
            "sight": sight,
            "last_result": result,
            "history": history,
        },
        indent=2,
    )
    _update_meta("research_generated_at", generated_at)
