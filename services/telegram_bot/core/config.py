"""환경 변수 로딩과 전역 설정 상수.

설정 저장 방침:
  - `.env` = 외부 공개되면 안 되는 비밀값·자격증명만(토큰, 비밀번호, 프록시
    URL처럼 값 안에 자격증명이 섞인 것, chat id처럼 운영자 개인 식별값).
    시작 시 1회, 이 모듈에서만 읽는다.
  - 그 외 모든 설정(기능 켜기·끄기, 수량·주기 같은 튜닝값 포함)은 이 모듈의
    리터럴 상수다. 값을 바꾸려면 코드를 고쳐야 하고 git에 남는다 — 바뀐
    이력을 서버 `.env`가 아니라 git이 갖고 있어야 무엇을 언제 왜 바꿨는지
    나중에 추적된다. 예외는 `CLOUDFLARE_MODEL` 하나뿐이다: Cloudflare가
    모델을 폐기·개명하면 이 값이 코드 배포 없이 즉시 바뀌어야 번역·분석이
    전부 죽는 걸 막을 수 있어서 env로 남긴다.
    다른 모듈은 여기서 상수를 import 하며 `os.environ`에 직접 접근하지 않는다.
  - `storage/bot/<기능키>/*.json` = 봇이 수집·축적하는 데이터(전송 이력,
    종목 DB 등)로, 소유 기능별 하위 디렉토리에 둔다. 설정값은 저장하지
    않는다. `storage/`는 웹·one-shot·쇼츠와 같이 쓰는 공유 저장소다
    (`code_guide.md`의 「공유 저장소」).

import 시 .env 로딩과 로깅 설정이 한 번 수행된다.
"""

import logging
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# AkShare 응답의 정규식 처리를 위해 object 문자열 방식을 사용한다.
pd.set_option("future.infer_string", False)

BASE_DIR = Path(__file__).resolve().parents[3]

load_dotenv(BASE_DIR / ".env")

# 공유 저장소(NAS). 봇·웹·one-shot·쇼츠가 같은 경로를 각자 설정으로 읽는다.
# 전체가 한 파일시스템이어야 원자적 교체(os.replace)가 성립한다.
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "").strip() or BASE_DIR / "storage")
# 공개 화면이 내보내는 산출물. 봇은 여기에 쓰고 웹은 읽기만 한다.
PUBLIC_DIR = STORAGE_DIR / "public"
# 개인 화면의 자산·관심종목·조언. 공개 라우트는 이 폴더를 내보내지 않는다.
PORTFOLIO_DIR = STORAGE_DIR / "portfolio"


class ConfigurationError(RuntimeError):
    """설정값이 잘못되어 봇을 기동할 수 없을 때 발생한다."""


FEATURES_ENABLED = frozenset(
    {
        "instruments",
        "sector_summary",
        "watchlist",
        "news_prefilter",
        "news_summary",
        "market_sentiment",
        "research",
        "briefing",
        "system_admin",
        "web_status",
        "shorts",
    }
)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
TELEGRAM_CONNECT_TIMEOUT_SECONDS = 10.0
TELEGRAM_READ_TIMEOUT_SECONDS = 20.0
TELEGRAM_WRITE_TIMEOUT_SECONDS = 20.0
TELEGRAM_POOL_TIMEOUT_SECONDS = 10.0
# 새 update가 오면 즉시 반환되므로 응답 지연은 늘지 않는다. 유휴 시에만
# getUpdates 재요청을 기본 10초보다 덜 자주 보내 CPU·네트워크 wakeup을 줄인다.
TELEGRAM_POLL_TIMEOUT_SECONDS = 30
TELEGRAM_CONCURRENT_UPDATES = 2
# 타임아웃을 넘기지 않는 `requests` 호출(akshare 내부 등)에 채우는 (연결, 읽기) 초.
# 없으면 응답 없는 소스 하나가 호출 하나를 수십 분 붙잡는다(core/http_timeout.py).
# 연결 대기는 도메인의 주소마다·akshare 재시도마다 되풀이된다 — 서버 실측에서 연결
# 10초가 막힌 sina 한 번에 94초였다. 5초로 둬 그 절반으로 줄인다. 막힌 뒤로는
# QuoteService의 실패 쿨다운(15분)이 바로 돌려보낸다.
DEFAULT_REQUESTS_TIMEOUT_SECONDS = (5.0, 30.0)
TELEGRAM_STATUS_MAX_ATTEMPTS = 2
TELEGRAM_STATUS_RETRY_DELAY_SECONDS = 0.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
# telegram.ext만 INFO로 둔다. 2026-09-21 12:08 종료가 90초 만에 SIGKILL됐는데
# journal이 비어 어느 단계에서 멈췄는지 가릴 수 없었다 — PTB가 INFO로 찍는
# "Application is stopping."과 "Application.stop() complete"가 그 구간을 가른다.
# 나머지 단계는 전부 DEBUG라 이 둘만 올리면 되고, 시끄러운 쪽은 위의 httpx와
# raw API 쪽 telegram 로거라 둘은 WARNING에 그대로 둔다.
logging.getLogger("telegram.ext").setLevel(logging.INFO)

# 봇 내부 상태는 소유 기능 키의 하위 디렉토리에 둔다.
# (news/, watchlist/, instruments/, research/, runtime/)
DATA_DIR          = STORAGE_DIR / "bot"
SENT_IDS_FILE     = DATA_DIR / "news" / "sent_ids.json"
NEWS_LOG_FILE     = DATA_DIR / "news" / "news_log.json"
NEWS_REPORT_QUEUE_FILE = DATA_DIR / "news" / "news_report_queue.json"
NEWS_REPORT_MEMORY_FILE = DATA_DIR / "news" / "news_report_memory.json"
WATCHLIST_FILE    = PORTFOLIO_DIR / "watchlist.json"  # 웹과 같이 쓰는 공유 파일(잠금)
WATCHLIST_EVENTS_FILE = DATA_DIR / "watchlist" / "watchlist_events.json"
STOCK_DB_FILE     = DATA_DIR / "instruments" / "stock_db.json"
RESEARCH_STATE_FILE = DATA_DIR / "research" / "market_research.json"
NEWS_PREFILTER_EVENT_FILE = DATA_DIR / "news_prefilter" / "event_memory.json"
NEWS_PREFILTER_OBSERVATION_FILE = DATA_DIR / "news_prefilter" / "observations.jsonl"
NEWS_PREFILTER_MODEL_FILE = DATA_DIR / "news_prefilter" / "model.json"
NEWS_PREFILTER_CPU_STATE_FILE = DATA_DIR / "news_prefilter" / "cpu_budget.json"
RUNTIME_LOCK_FILE = DATA_DIR / "runtime" / "bot.lock"
PROMPT_DIR        = Path(__file__).resolve().parents[1] / "prompts"

# ── 번역 ──────────────────────────────────────────────
TRANSLATION_ENABLED = True
# 기사 본문이 200자 내외라 출력 토큰 상한도 함께 내렸다. 남겨 둘 이유가 없다 —
# 이 값이 곧 한 기사의 최대 지연이다. 다만 잘리면 JSON 파싱이 실패해 그 기사가
# 통째로 버려지므로, 제목·종목 배열까지 합친 봉투에 여유를 두고 잡았다.
TRANSLATION_NUM_PREDICT = 768

# ── Cloudflare Workers AI ─────────────────────────────
# API 토큰은 .env에만 두고 커밋하지 않는다. 로그·예외에도 남기지 않는다.
CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
CLOUDFLARE_AI_BASE_URL = "https://api.cloudflare.com/client/v4"
# 번역과 분석은 같은 모델을 쓴다. 나눌 실익이 없다 — `qwen3-30b-a3b`는 이름과
# 달리 MoE(활성 3B)라 단가가 3B 모델과 같다(입력 $0.0509/M, 출력 $0.335/M).
# "가벼운 번역 / 무거운 분석"으로 나눠도 절감이 0이다.
# 값 자체는 env로 남긴다. 임계값과 달리 모델 이름은 Cloudflare가 폐기·개명하면
# 외부 사정으로 무효가 되므로, 코드 배포 없이 고칠 수 있어야 한다.
CLOUDFLARE_MODEL = os.environ.get(
    "CLOUDFLARE_MODEL", "@cf/qwen/qwen3-30b-a3b-fp8"
).strip()
CLOUDFLARE_TRANSLATION_TIMEOUT = 45
CLOUDFLARE_MAX_ATTEMPTS = 2
CLOUDFLARE_FAILURE_THRESHOLD = 3
CLOUDFLARE_FAILURE_COOLDOWN_SECONDS = 300


# /research 분석과 /market 다이제스트는 전용 플래그 없이 기능 키가 켜지면
# 항상 LLM을 쓴다. 그래서 자격증명 요구 여부는 FEATURES_ENABLED로 판단한다.
_LLM_FEATURE_KEYS = frozenset({"research", "market_sentiment"})
# 브리핑 절 전체(BRIEFING_*)보다 먼저 정의한다 — 아래 검증기가 모듈 로딩
# 중간(줄 141)에 곧바로 불려 그 시점에 이미 값이 있어야 한다.
BRIEFING_LLM_ENABLED = True


def _validate_cloudflare_credentials() -> None:
    """자격증명 없이 기동해서 첫 뉴스 주기에 전부 실패하는 일을 막는다."""
    if not (
        TRANSLATION_ENABLED
        or (FEATURES_ENABLED & _LLM_FEATURE_KEYS)
        or BRIEFING_LLM_ENABLED
    ):
        return
    missing = [
        name
        for name, value in (
            ("CLOUDFLARE_ACCOUNT_ID", CLOUDFLARE_ACCOUNT_ID),
            ("CLOUDFLARE_API_TOKEN", CLOUDFLARE_API_TOKEN),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(
            "LLM 기능(번역·리서치·시황 다이제스트·브리핑)이 켜져 있으나 "
            f"{', '.join(missing)}이(가) .env에 비어 있습니다"
        )


_validate_cloudflare_credentials()

SENT_NEWS_RETENTION_DAYS = 7
TELEGRAM_MESSAGE_LIMIT = 4096
NEWS_DIGEST_MESSAGE_MAX_CHARS = 3500
# 다이제스트 한 기사의 제목·본문 표시 상한. 프롬프트도 본문을 200자 내외로
# 지시하지만 그것은 지시일 뿐이라, 모델이 길게 답하는 주기가 섞이면 메시지가
# 다시 부풀고 chunk_message_items가 메시지 수만 늘린다. 표시 단계에서 상한을
# 확정해 한 주기에 올라오는 총량을 예측 가능하게 둔다. 운영자가 조정하는 값이
# 아니라 읽기 경험의 규약이므로 env가 아닌 상수다.
NEWS_DIGEST_ARTICLE_MAX_CHARS = 220
NEWS_DIGEST_TITLE_MAX_CHARS = 80
NEWS_SOURCE_FETCH_TIMEOUT_SECONDS = 45.0
NEWS_LIVE_MAX_AGE_HOURS = 48
# 소스 한 곳을 얼마나 깊이 읽을지. 사전선별이 훑는 후보의 폭이고, 여기서
# 잘린 기사는 다음 주기에도 목록에 남지 않으면 영영 보이지 않는다. 60분
# 주기에서는 한 주기가 덮어야 할 시간이 3배라 이 깊이가 더 중요해졌다.
# gnews는 이 값을 시장 수로, gnews_us·gnews_kr은 질의 수로 다시 나눠 쓴다.
NEWS_SOURCE_ARTICLE_LIMIT = 250

# ── 시장상황 보고서 ──────────────────────────────────
# 매시간 원문만 수집하고 UTC +9 03·08·15·20시에 **발행할지부터 판정한다.**
# 검토 시각은 검토 시점이고 발행 주기가 아니다 — 재료가 얇거나 직전 보고서의
# 판단이 그대로인 구간에 한 편을 억지로 쓰게 하면 같은 국면을 다른 문장으로
# 반복하게 되고, 그 반복이 보고서를 기계적으로 만든다. 보류한 시장의 기사는
# 큐에 남아 다음 구간에 더 두꺼운 재료로 다시 평가된다.
# 기사별 번역은 예약 실행하지 않는다 — 호출량은 기사 수가 아니라 발행·검토한
# 시장 수에 비례하고, 1차 게이트에 걸린 시장은 LLM을 부르지도 않는다.
NEWS_COLLECTION_INTERVAL_MINUTES = 60
# 4시간 간격(00·04·08·12·16·20시)에서 두 시장의 장 앞뒤 네 시각으로 바꿨다(운영자 결정,
# 2026-10-06). 한국장(09:00~15:30)과 미국장(한국 시간 22:30~05:00, 서머타임 해제 때 한 시간 늦다)의
# 개장 전후다. 간격은 5·7·5·7시간으로 고르지 않다 — 구간 길이를 이 값에서 셈하지 말고
# 이전 검토 시각(`news/report.py`의 `_previous_slot`)을 쓴다. 표시 이름은 머리말과 모델 입력의 `session`이다.
NEWS_REPORT_SESSIONS = {
    3: "미국장 개장 후",
    8: "한국장 개장 전",
    15: "한국장 마감 전후",
    20: "미국장 개장 전",
}
NEWS_REPORT_HOURS = tuple(sorted(NEWS_REPORT_SESSIONS))
NEWS_REPORT_PROMPT_FILE = PROMPT_DIR / "news_report_ko.txt"
NEWS_REPORT_TIMEOUT = 180
# 출력 예약. **2048은 상시로 모자랐다** — 2026-09-17 하루에만 06시 CN·US·KR과
# 03시 US가 정확히 2048에서 잘려(`finish_reason=length`) 보고서가 원문 제목
# 나열로 떨어졌다. 입력은 2,832~3,601 토큰으로 작았으므로 원인은 입력이 아니다.
# 한국어는 토큰이 비싸서 analysis 400~500자에 번역 제목 8건·evaluations까지
# 얹으면 2048에 닿는다. 컨텍스트 32,768에 견줘 여유가 크고, 무료 한도 대비
# 소비도 하루 1,349/10,000(실측 2026-09-17)이라 올릴 자리가 있다.
# 2026-10-02 근거·평가가 원문 제목(source_title)을 그대로 복사하게 되며 출력이 늘었다. 같은 날
# 중국 실측 1,401·1,744토큰이 보통이지만 한 번은 4,037토큰으로 4096 상한 직전까지 갔다 — 잘리면
# 재시도 없이 그 시장 보고서가 실패한다. 상한은 쓴 만큼만 과금되므로 6144로 둔다
# (입력 최대 약 16,000토큰 + 6,144 < 컨텍스트 32,768).
NEWS_REPORT_NUM_PREDICT = 6144
# 본문을 450~650자로 늘리면서 함께 올렸다. 900~1,300자(2026-10-06)는 약 2,000토큰이라
# 근거·평가·판정을 더해도 3,600 선이고 6144 안이다. 650자는 한국어 토큰으로 약 1,000이고
# 근거 기사 8건(제목 80자·부가 필드)이 약 1,300, evaluations 10건이 약 150,
# 발행 판정 두 필드가 약 100이라 합이 2,600 선이다. 3584에 남는 여유가
# 900토큰뿐이라 highlights가 상한까지 찬 구간에서 다시 length에 닿는다.
# 큐에 담는 상한. 수집은 LLM을 부르지 않으며 보고서가 여러 사건을 비교할
# 폭을 확보한다. 사전선별도 이 상한으로 점수·탐색 슬롯을 배정한다.
NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT = 12
# 한 구간이 담는 총량. 주기를 3→4시간으로 늘리며 600→720(2026-09-24), 가장 긴 구간이
# 7시간이 되며(2026-10-06) 7/4배인 1,260으로 올렸다.
# 소스당 상한(12)은 매시간 수집 한 번의 몫이라 주기와 무관해 그대로 둔다.
NEWS_REPORT_QUEUE_MAX_ITEMS = 1260
# 시장 하나의 보고서에 넣을 헤드라인 수와, 근거로 뽑아 보여줄 건수.
# 이보다 오래된 기사는 모델이 읽지 않은 채 발행과 함께 확정된다. 216건이면 입력이 약
# 10,800토큰이라 출력 예약 6,144를 더해도 컨텍스트 32,768의 52% 선이다. 올릴 때는 이 계산을
# 다시 한다. 주기를 3→4시간으로 늘리며 120→144(2026-09-24), 구간이 최대 7시간이 되며
# 144→216(2026-10-06). 고를 폭이 넓어야 근거를 엄격하게 고를 수 있다.
NEWS_REPORT_MAX_HEADLINES = 216
NEWS_REPORT_MAX_HIGHLIGHTS = 8
# 근거 기사 수는 그 시장이 그 구간에 실제로 모은 기사 수에 비례시킨다.
# 고정 8은 수집이 얇은 시장에서 무리한 요구가 된다 — 실측(2026-09-02 한국)에서
# 11건을 주고 8건을 고르라고 했다. 전체의 73%는 선별이 아니라 목록 복사이고,
# 채울 것이 모자라면 모델이 없는 것을 만든다(title 누락, 같은 index 반복).
# 11건→3, 20건→5, 32건 이상→8. 큰 시장은 지금과 같다.
NEWS_REPORT_HIGHLIGHT_RATIO = 0.25
NEWS_REPORT_MIN_HIGHLIGHTS = 3
# 화면에 붙이는 근거는 그중 앞 몇 건까지다. 나머지도 모델이 고른 근거라
# NewsLog와 사전선별 라벨에는 그대로 들어간다 — 줄이는 것은 표시 분량이지
# 라벨 공급량이 아니다. 본문이 판단이고 목록은 그 각주다.
NEWS_REPORT_SHOWN_HIGHLIGHTS = 4
# ── 발행 판정 ────────────────────────────────────────
# 1차: 그 시장이 마지막 발행 뒤 모은 기사가 이만큼도 안 되면 LLM을 부르지 않고
# 보류한다. 2차: 모델이 직전 보고서 대비 새로 확인된 사실·방향 전환이 없다고
# 판정하면 보류한다.
NEWS_REPORT_MIN_ARTICLES = 8
# 연속 보류 상한. 이 시간을 넘기면 판정과 무관하게 발행한다.
# **사전선별의 라벨 공급원은 이 보고서 하나뿐이라** 무한 보류는 학습선을
# 조용히 끊는다(2026-08-30~09-12에 같은 선이 끊겨 13일간 라벨 0건으로 돌았다).
NEWS_REPORT_MAX_HELD_HOURS = 12
# 2차(모델의 보류 판정)를 건너뛰고 매 구간 발행하는 시장(운영자 결정, 2026-10-01).
# 독자가 가장 자주 보는 두 시장이라 "새로운 것이 없다" 보류가 보고서에서 시장이 빠진
# 것으로 읽혔다(9/25~10/1 미국 13회·한국 18회 보류). 판정은 같은 호출 안에서 하므로
# 추가 Neurons는 0이다. 1차(재료 하한)와 아래 중복 차단은 그대로 걸린다.
NEWS_REPORT_ALWAYS_PUBLISH_MARKETS = frozenset({"US", "KR"})
# 새 본문이 직전 발행분과 이 비율 이상 같으면 판정과 무관하게 보류한다. 모델이 previous를
# 거의 그대로 다시 써 "발행"한 적이 있다(2026-10-01 미국 12·16시, 본문이 글자째 같았다).
# difflib 비율이라 비용이 없다.
NEWS_REPORT_DUPLICATE_RATIO = 0.9
# 직전 발행 보고서를 시장별로 기억한다. 다음 호출 입력에 넣어 "지난번 관찰
# 포인트가 확인됐는가"를 쓰게 하는 자리다 — 이 기억이 없으면 매 보고서가
# 무상태라 비교 대상 없이 같은 국면을 새 얘기처럼 다시 쓴다.
NEWS_REPORT_MEMORY_RETENTION_DAYS = 30

# ── 보고서 후보 사전선별·로컬 사건 메모리 ─────────────
# 운영자 요청으로 active 실험을 시작한다. 점수 효과가 입증됐다는 뜻은 아니다.
# 소스당 12건 중 중요도 상위 10건 + 무작위 탐색 2건을 보고서 큐로 보낸다.
NEWS_PREFILTER_MODE = "active"
NEWS_PREFILTER_EVENT_WINDOW_HOURS = 72
NEWS_PREFILTER_MAX_EVENTS = 5000
NEWS_PREFILTER_OBSERVATION_RETENTION_DAYS = 7
NEWS_PREFILTER_SIMILARITY_THRESHOLD = 0.74
NEWS_PREFILTER_EXPLORATION_SLOTS = 2
# 저장된 사건의 translated_at은 지금은 보고서 근거로 사용된 마지막 시각이다.
NEWS_PREFILTER_REPORTED_EVENT_COOLDOWN_HOURS = 24
# 장 시황(지수 등락을 받아 적은 기사)은 시장·지수마다 이 시간 구간(수집 시각 기준)에 가장 최근 한 건만
# 큐 후보로 둔다. 보고서 구간(5~7시간)마다 두세 건이라 지수가 어디서 어디로 갔는지는 남는다.
# 이 시간보다 오래 전에 발행된 장 시황은 이미 지난 지수 위치라 대표로 고르지 않는다.
NEWS_PREFILTER_RECAP_BUCKET_HOURS = 3
NEWS_PREFILTER_RECAP_MAX_AGE_HOURS = 6

# 새 라벨에 대한 학습을 단일 worker에서 수행한다. 고정 CPU 비율·일일 상한은 없다.
# 한 번의 예약 실행은 최대 30 CPU초, 2초 조각 사이에 긴급 작업·호스트 부하를 확인한다.
# 자료당 32 trial이 끝나면 새 라벨까지 쉰다. 이 값은 일일 소비 목표가 아니다.
NEWS_PREFILTER_MAINTENANCE_INTERVAL_MINUTES = 1
NEWS_PREFILTER_MAINTENANCE_MAX_SECONDS = 30.0
NEWS_PREFILTER_MAINTENANCE_CHUNK_SECONDS = 2.0
NEWS_PREFILTER_MAX_LOAD_AVERAGE = 1.5


# 원문 1차 소스를 시장마다 하나씩 둔다. 통신사·규제기관 발표는 매체가 받아쓰기
# 전에 나오므로, 집계 소스(Google News)만으로는 같은 사실을 한 박자 늦게 본다.
#
# 시장이 얇을 때 수집량을 늘려도 소용이 없다. NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT
# 은 **소스당**이라 일곱 시장을 덮는 `gnews` 하나로는 그 시장에 두 건 남짓만
# 돌아가고, 3주기를 모아도 NEWS_REPORT_MIN_ARTICLES(8)에 못 미쳐 발행 게이트에
# 걸린다. 전용 소스라야 자기 몫의 슬롯을 받는다 — `gnews_jp`를 세운 이유다.
#
# 속보 소스는 쓰지 않는다(운영자 결정 2026-10-02). 목적은 빠른 기사 취득이 아니라
# 사건과 그 함의의 분석이다. 그때 큐의 중국 314건 중 312건이 7×24 속보(`em_global`·
# `futu`)였고, 보고서는 그 한 줄짜리 快讯을 국면 분석의 재료로 쓰고 있었다. 넷(futu·
# em_global·sina·cls)의 어댑터를 지웠다 — sina·cls는 그 전에 접속·수급 문제로 빠져
# 있었지만 같은 속보 피드라 같이 퇴역했다. 중국 칸은 `gnews_cn`(간체 로케일의 분석·
# 정책 질의)과 `gnews`의 영어 질의가 채운다. 다른 소스의 【速報】·[속보] 제목은
# 수집 단계에서 거른다(`news/utils.py`의 `is_flash_title`).
#
# 유럽(EU)은 2026-09-30에 여섯 번째 시장으로 올렸다. 그전에는 `gnews` 혼합 질의
# 하나로만 들어와 큐 슬롯이 두 건 남짓이라 보고서가 보류 한도(12시간)에 걸려야
# 나갔다. `gnews_eu`가 자기 슬롯을 받고, `ecb-press`가 1차 소스 자리를 채운다.
# Reuters·Bloomberg·FT는 공개 RSS가 없어 Google News 질의로만 들어온다.
# 같은 날 기존 시장도 보강했다 — 일본은 1차 소스가 없어 `boj-whatsnew`(일본은행)와
# 공영방송 `nhk-economy`를, 홍콩은 `gnews` 혼합 질의 하나뿐이라 공영방송
# `rthk-finance`를, 미국은 독자층이 두터운 `cnbc-finance`를 더했다.
# 배포 뒤 서버에서 확인했다(2026-09-30): 다섯 곳은 첫 수집에서 큐까지 들어왔고, NHK는
# 옛 주소(www3.nhk.or.jp/rss/news/cat5.xml)가 열리기는 하나 8월 8일 이후 갱신이 멈춰
# 48시간 필터에 전부 걸렸다 — NHK가 옮긴 새 주소(news.web.nhk)로 바꿨다.
# 닿지 않는 피드는 레지스트리가 쿨다운으로 쉬게 한다.
NEWS_GLOBAL_SOURCE_KEYS = [
    "gnews_cn", "gnews", "gnews_us", "gnews_kr", "gnews_jp", "gnews_eu",
]
NEWS_RSS_FEEDS: list[tuple[str, str]] = [
    ("mk-stock", "https://www.mk.co.kr/rss/50200011/"),
    ("yonhap-economy", "https://www.yna.co.kr/rss/economy.xml"),
    ("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("cnbc-finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("ecb-press", "https://www.ecb.europa.eu/rss/press.html"),
    ("boj-whatsnew", "https://www.boj.or.jp/rss/whatsnew.xml"),
    ("nhk-economy", "https://news.web.nhk/n-data/conf/na/rss/cat5.xml"),
    ("rthk-finance", "https://rthk.hk/rthk/news/rss/c_expressnews_cfinance.xml"),
]
NEWS_SOURCE_FAILURE_THRESHOLD = 3
# 주기가 60분이라 60분 쿨다운은 한 주기도 쉬지 못하고 곧바로 다시 불린다.
# 연속 실패한 소스는 두 주기를 쉬게 둔다.
NEWS_SOURCE_COOLDOWN_MINUTES = 120
# 뉴스 메시지에 감성 점수 표기 여부.
NEWS_SENTIMENT_ENABLED = True
# ── 시황 리서치(/research) ────────────────────────────
RESEARCH_ANALYSIS_PROMPT_FILE = PROMPT_DIR / "market_research_ko.txt"
RESEARCH_ANALYSIS_TIMEOUT = 600
RESEARCH_NEWS_MAX_ITEMS = 16
RESEARCH_NEWS_GLOBAL_LIMIT = 8
# 분석 payload에 넣을 기사 본문 길이 상한. 제목만으로는 촉매·수치를 읽을 수
# 없어 분석이 얕아지므로 본문을 함께 넣는다. 16건 × 600자에 후보 24개·요약·
# 이력까지 상한을 가득 채우면 입력이 약 22,000토큰(보수 추정)이고, 출력 예약
# 4,096을 더해 약 26,000으로 컨텍스트 32,768의 79% 선이다. 이 값이나
# RESEARCH_NEWS_MAX_ITEMS·RESEARCH_MAX_CANDIDATES를 올릴 때는 남은 21%를
# 어디까지 쓰는지 다시 계산한다 — 넘기면 응답이 중간에서 잘려 파싱이 실패한다.
RESEARCH_NEWS_CONTENT_MAX_CHARS = 600
# 리서치 뉴스 수집에서 균형을 맞출 시장 순서. 소스 우선순위대로 뽑으면 첫 소스
# (중화권)가 상한을 독식해 미국·한국 뉴스가 분석 입력에 들어가지 못한다.
# 여기 적힌 시장끼리 라운드로빈으로 뽑고, 목록 밖 시장은 마지막에 채운다.
RESEARCH_NEWS_MARKETS = ("CN", "US", "KR")
# 분석 결과는 JSON 한 덩어리로 오므로 상한에 걸리면 문자열 중간에서 잘려
# 파싱이 실패한다. 2026-08-15에 4096, 2026-08-24에 8192가 각각 output_tokens에
# 정확히 걸려 `Unterminated string`으로 죽었다.
# **상한을 올리는 것이 답이 아니다.** 두 번째 실패 때 입력이 이미 20,612토큰이라
# 16,384으로 올리면 컨텍스트 32,768을 넘긴다. 원인은 용량 부족이 아니라 출력
# 스키마가 evidence마다 원문 URL(Google News 리다이렉트, 중앙값 286자 base64)을
# 받아 적게 한 것이었다 - 25개가 출력의 3분의 1을 먹으면서 정작 아무 화면에도
# 그려지지 않았다. evidence를 news_items의 id 참조로 바꿔(`_news_payload`) 최대
# 크기 응답이 5,641 → 2,063토큰이 됐고, 8192는 이제 4배 여유다.
# 이 값을 다시 만지기 전에 무엇이 출력을 채우고 있는지부터 센다.
RESEARCH_ANALYSIS_NUM_PREDICT = 8192
RESEARCH_MAX_CANDIDATES = 24
# 뉴스 본문 종목명 매칭에서 버릴 '흔한 영문 토큰'의 기준(이 수보다 많은 종목이
# 공유하는 토큰은 사용하지 않는다).
STOCK_NAME_TOKEN_MAX_FREQUENCY = 15
RESEARCH_MAX_NEW_ACTIONS = 6
# 후보 상한 중 시장별 발굴에 남겨 둘 자리.
RESEARCH_DISCOVERY_RESERVED_SLOTS = 8
NON_URGENT_WORKER_COUNT = 3
# 뉴스 주기가 도는 동안 비긴급 LLM 작업을 보류할 최대 시간(초). 한도를 넘으면
# 굶지 않도록 그대로 진행한다. 0이면 보류하지 않는다.
NON_URGENT_DEFER_TIMEOUT_SECONDS = 180
RESEARCH_REMOVE_RELEVANCE_THRESHOLD = 0.35
RESEARCH_HISTORY_LIMIT = 5
# 강세 섹터 구성종목을 리서치 후보군에 추가
RESEARCH_SECTOR_CANDIDATES_ENABLED = True
RESEARCH_SECTOR_CANDIDATE_LIMIT = 14
# 미국 후보 발굴: Yahoo Finance 프리셋 스크리너(yfinance.screen).
RESEARCH_US_CANDIDATES_ENABLED = True
RESEARCH_US_CANDIDATE_LIMIT = 12
RESEARCH_US_SCREENERS = ("day_gainers", "most_actives")
# 한국 후보 발굴: FinanceDataReader KRX 시세 목록의 등락률 상위.
RESEARCH_KR_CANDIDATES_ENABLED = True
RESEARCH_KR_CANDIDATE_LIMIT = 12
# 거래대금(원) 하한. 급등만 보고 잡주를 추천 후보로 올리지 않기 위한 필터.
RESEARCH_KR_MIN_TRADING_VALUE = 5000000000.0

# 요약 컨텍스트(시세·자금흐름·섹터·涨停·용호방)
SECTOR_SUMMARY_CACHE_TTL_MINUTES = 10
SECTOR_SUMMARY_SECTOR_TOP_N = 5
SECTOR_SUMMARY_FAILURE_COOLDOWN_MINUTES = 15

# 최근 뉴스 로그(마감 브리핑 요약 입력)
NEWS_LOG_RETENTION_DAYS = 30
# Source-to-market mapping for the market sentiment dashboard.  Values are
# ISO-like market keys (CN, HK, US, KR, JP, ...); an unmapped source is kept as
# "OTHER" instead of being silently mixed into another market.
NEWS_SOURCE_MARKETS = {
    "gnews_cn": "CN",
    "gnews_jp": "JP",
    "gnews_us": "US",
    "gnews_kr": "KR",
    "gnews_eu": "EU",
    "mk-stock": "KR",
    "yonhap-economy": "KR",
    "fed-press": "US",
    "cnbc-finance": "US",
    "ecb-press": "EU",
    "boj-whatsnew": "JP",
    "nhk-economy": "JP",
    "rthk-finance": "HK",
}
# 시장 감성 예약 갱신의 조회 일수와 대상 시장 집합. 웹 화면이 이 기간의 차트를 그린다.
# 30일로 넓혔다(2026-09-28). 차트는 누적 논조선을 그린다(2026-10-04). 다이제스트 보관(30일)과 같다.
MARKET_CHART_LOOKBACK_DAYS = 30
MARKET_CHART_MARKETS = frozenset({"CN", "HK", "US", "KR", "JP", "EU"})
# 아래는 일별 감성 다이제스트 전용이다.
MARKET_CHART_MIN_ARTICLES = 6
MARKET_CHART_MIN_DAYS = 3
MARKET_CHART_BACKFILL_DAYS_PER_REQUEST = 7

# 일별 감성 다이제스트는 하루치 헤드라인을 한 번에 분석한다.
MARKET_DIGEST_FILE = DATA_DIR / "market_sentiment" / "daily_digest.json"
MARKET_DIGEST_PROMPT_FILE = PROMPT_DIR / "market_digest_ko.txt"
MARKET_DIGEST_ARTICLES_PER_DAY = 40
# 표본이 이보다 적은 날은 계산하지 않는다. 3건짜리 하루를 20건짜리 하루와 같은
# 무게로 그리면 차트가 다시 출렁인다.
MARKET_DIGEST_MIN_ARTICLES = 5
# 최대 조회 범위(30일)와 맞춘다. 그보다 오래된 항목은 차트가 읽지 않는다.
MARKET_DIGEST_RETENTION_DAYS = 30
# 요청당 LLM 호출 상한. 40회 ≈ 350 Neurons.
MARKET_DIGEST_MAX_CALLS_PER_REQUEST = 40
# 헤드라인 40건의 점수 목록 + 요약 한두 문장. 점수 목록이 붙어 512에서 올렸다(2026-09-30).
MARKET_DIGEST_NUM_PREDICT = 900
MARKET_DIGEST_TIMEOUT = 60
# 헤드라인 점수가 입력 헤드라인 수보다 이만큼 넘게 모자라면 점수 평균을 버리고 모델의
# 종합 판단으로 대체한다(그날의 summary는 남긴다). 허용 오차 = max(1, ceil(헤드라인 수 × 이 비율)).
# 이 비율은 `MARKET_DIGEST_ARTICLES_PER_DAY`와 함께 봐야 한다. 모델의 세기
# 오차는 목록이 길어질수록 비례 이상으로 커진다 — 20건 시절 실측은 93일 중
# 78일이 오차 0, 최대 2였지만 35~40건에서는 5까지 벌어졌다. 상한을 40으로
# 올리면서 0.1(35건 → 허용 4)로는 정상 응답이 탈락했다.
MARKET_DIGEST_COUNT_TOLERANCE_RATIO = 0.2


NEWS_MARKET_BACKFILL_QUERIES = {
    "CN": "China stock market",
    "HK": "Hong Kong stock market",
    "US": "US stock market",
    "KR": "Korea stock market",
    # 영어로 둔다. 날짜별 이력 조회(fetch_google_news_history)는 시장 로케일 없이
    # 기본(en-US) 로케일로 부르는데, 일본어 질의는 그 로케일에서 0건이라 2026-09-24까지
    # 감성 추이에 일본이 통째로 빠졌다. 영어 질의가 하루 17~25건으로 가장 많았다
    # (일본어+ja 로케일 11~19건). 다른 시장도 전부 영어 질의다.
    "JP": "Japan stock market",
    "EU": "European stock market",
    "RU": "Russia stock market",
    "TW": "Taiwan stock market",
}

# 모닝/마감 브리핑(JST 기준 cron). 스케줄러에 시간대를 넘기지 않으면 호스트 시간대를
# 따르는데, 공유 호스트가 UTC라 2026-09-24 전까지 모닝 브리핑이 18시대(JST)에 나갔다.
# BRIEFING_LLM_ENABLED는 위(줄 120)에 이미 정의돼 있다 — Cloudflare 자격증명
# 검증기가 모듈 로딩 중간에 그 값을 곧바로 써야 해서 앞으로 옮겼다.
BRIEFING_MORNING_ENABLED = True
BRIEFING_MORNING_HOUR = 8
BRIEFING_MORNING_MINUTE = 50
BRIEFING_EVENING_ENABLED = True
BRIEFING_EVENING_HOUR = 17
BRIEFING_EVENING_MINUTE = 40
# 수동 브리핑은 JST 기준 아시아 시장 세션에 맞춰 장전·장중·장후를 고른다.
# 17시는 한국(15:30), 중국(16:00 JST), 홍콩(17:00 JST)이 모두 끝나는 경계다.
BRIEFING_MARKET_OPEN_HOUR = 9
BRIEFING_MARKET_OPEN_MINUTE = 0
BRIEFING_MARKET_CLOSE_HOUR = 17
BRIEFING_MARKET_CLOSE_MINUTE = 0
BRIEFING_NEWS_MAX_ITEMS = 14

# ── 예약 리서치·시장 감성(JST 기준 cron) ─────────────────
# 둘 다 정해진 시각에 돌고, 결과는 웹(storage/public)과 봇이 같은 한 벌을 읽는다.
# 텔레그램은 웹의 관리 패널이다 — 주제 조작과 "지금 실행"만 거기서 한다.
# 리서치는 모닝 브리핑(08:50) 전에 끝나야 브리핑이 그날 결과를 쓴다.
# 한 번에 LLM 호출 한 번이지만 뉴스 수집·후보 구성까지 수 분이 걸린다.
RESEARCH_SCHEDULE_HOUR = 8
RESEARCH_SCHEDULE_MINUTE = 20
# 웹 개인 리서치의 입력 묶음(`storage/public/research_inputs.json`)을 굽는 주기. 웹 계정은 버튼을 누를 때
# 이 묶음으로 분석하므로, 뉴스 신선도(`NEWS_LIVE_MAX_AGE_HOURS`) 안에서 너무 오래되지 않게 4시간마다 굽는다.
# 정각(보고서)·40분(시장 감성)·58분(뉴스 수집)을 피한다.
RESEARCH_INPUTS_SCHEDULE_HOURS = "*/4"
RESEARCH_INPUTS_SCHEDULE_MINUTE = 30
# 시장 감성은 오늘 치만 다시 계산하고 지난 날은 저장값을 재사용한다(시장당 1회 호출).
# 하루 세 번이면 아시아 장 전·장 마감 뒤·미장 전에 한 번씩 갱신된다.
MARKET_SENTIMENT_SCHEDULE_HOURS = (7, 13, 19)
MARKET_SENTIMENT_SCHEDULE_MINUTE = 40

# 관리 패널의 웹 상태(/web). 봇은 웹 코드를 import하지 않고 같은 호스트의 공개 웹
# GET API를 HTTP로 읽는다(shorts와 같은 방식). 웹은 루프백 8788에만 떠 있다.
WEB_STATUS_BASE_URL = "http://127.0.0.1:8788"
WEB_STATUS_TIMEOUT_SECONDS = 5
BRIEFING_NEWS_MARKETS = ("CN", "HK", "US", "KR", "JP", "EU")
BRIEFING_PROMPT_FILE = PROMPT_DIR / "briefing_ko.txt"
BRIEFING_TIMEOUT = 180
# 코멘트 출력 예약 토큰. 헤드라인을 늘린 만큼 코멘트도 길게 받는다.
BRIEFING_NUM_PREDICT = 1024


def _parse_allowed_chat_ids() -> frozenset[int]:
    """명령을 받을 chat_id 목록. 하나도 없으면 기동하지 않는다.

    빈 목록을 "모두 허용"으로 읽으면 안 된다 — 봇 사용자명을 아는 누구나
    관심종목·리서치 상태를 읽고 고치며 LLM 호출로 Neurons를 태울 수 있다.
    상태는 채팅별로 나뉘어 있지 않아 공개 전제가 성립하지 않는다.
    오타로 유효한 값이 하나도 남지 않은 경우도 같게 취급한다. 그쪽이 더
    위험하다 — 설정했다고 믿는 채로 전체 허용이 된다.
    """
    raw = os.environ.get("ALLOWED_CHAT_IDS", "").strip()
    ids: set[int] = set()
    invalid: list[str] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError:
            invalid.append(chunk)
    if invalid:
        logging.getLogger(__name__).warning(
            "ALLOWED_CHAT_IDS에 숫자가 아닌 값이 있어 무시합니다: %s",
            ", ".join(invalid),
        )
    if not ids:
        raise ConfigurationError(
            "ALLOWED_CHAT_IDS에 유효한 chat_id가 없습니다. 명령을 받을 채팅 ID를 "
            "쉼표로 구분해 .env에 적습니다"
        )
    return frozenset(ids)


# 여기 있는 chat_id에서 온 업데이트만 처리한다. 비면 위에서 기동이 멈춘다.
ALLOWED_CHAT_IDS = _parse_allowed_chat_ids()


# ── 쇼츠 운영(텔레그램 /shorts) ──────────────────────────
# 쇼츠는 자기 venv를 가진 별개 패키지다. 봇은 import하지 않고 그 venv의 파이썬으로
# CLI를 하위 프로세스로 부른다(code_guide.md). 산출물은 공유 저장소 storage/shorts/.
SHORTS_PYTHON = Path(
    os.environ.get("SHORTS_PYTHON", "").strip() or BASE_DIR / "shorts" / ".venv" / "bin" / "python"
)
SHORTS_WORKDIR = BASE_DIR
# 예약 제작 시각. infra/systemd/polymarket-shorts.timer의 OnCalendar와 같아야 한다
# (test_shorts_panel.py가 대조한다). 표시용이며 실행은 timer가 한다.
SHORTS_SCHEDULE_HOUR = 20
SHORTS_STATUS_TIMEOUT_SECONDS = 60
# 제작은 이슈 선별·TTS·렌더까지 유닛의 TimeoutStartSec(20분)과 같게 둔다.
SHORTS_RUN_TIMEOUT_SECONDS = 20 * 60
SHORTS_EDIT_TIMEOUT_SECONDS = 15 * 60
# 텔레그램 봇 API의 파일 업로드 상한. 넘으면 경로만 알린다.
SHORTS_TELEGRAM_VIDEO_MAX_BYTES = 50 * 1024 * 1024
