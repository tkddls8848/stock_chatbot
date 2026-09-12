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
  - `data/<기능키>/*.json` = 봇이 수집·축적하는 데이터(관심종목, 전송 이력,
    종목 DB 등)로, 소유 기능별 하위 디렉토리에 둔다. 설정값은 저장하지
    않는다.

import 시 .env 로딩과 로깅 설정이 한 번 수행된다.
"""

import logging
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# AkShare 응답의 정규식 처리를 위해 object 문자열 방식을 사용한다.
pd.set_option("future.infer_string", False)

BASE_DIR = Path(__file__).resolve().parents[2]

load_dotenv(BASE_DIR / ".env")


class ConfigurationError(RuntimeError):
    """설정값이 잘못되어 봇을 기동할 수 없을 때 발생한다."""


FEATURES_ENABLED = frozenset(
    {
        "instruments",
        "quant",
        "watchlist",
        "news_prefilter",
        "news",
        "market_sentiment",
        "research",
        "briefing",
        "signal_scoring",
        "system_admin",
        "web_admin",
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
TELEGRAM_STATUS_MAX_ATTEMPTS = 2
TELEGRAM_STATUS_RETRY_DELAY_SECONDS = 0.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# 데이터는 코드와 같은 기준으로 소유 기능 키의 하위 디렉토리에 둔다.
# (news/, watchlist/, instruments/, signal_scoring/, research/, runtime/)
DATA_DIR          = BASE_DIR / "data"
SENT_IDS_FILE     = DATA_DIR / "news" / "sent_ids.json"
NEWS_LOG_FILE     = DATA_DIR / "news" / "news_log.json"
NEWS_REPORT_QUEUE_FILE = DATA_DIR / "news" / "news_report_queue.json"
WATCHLIST_FILE    = DATA_DIR / "watchlist" / "watchlist.json"
WATCHLIST_EVENTS_FILE = DATA_DIR / "watchlist" / "watchlist_events.json"
STOCK_DB_FILE     = DATA_DIR / "instruments" / "stock_db.json"
PREDICTION_LOG_FILE = DATA_DIR / "signal_scoring" / "prediction_log.jsonl"
RESEARCH_STATE_FILE = DATA_DIR / "research" / "market_research.json"
NEWS_PREFILTER_EVENT_FILE = DATA_DIR / "news_prefilter" / "event_memory.json"
NEWS_PREFILTER_OBSERVATION_FILE = DATA_DIR / "news_prefilter" / "observations.jsonl"
NEWS_PREFILTER_MODEL_FILE = DATA_DIR / "news_prefilter" / "model.json"
NEWS_PREFILTER_CPU_STATE_FILE = DATA_DIR / "news_prefilter" / "cpu_budget.json"
RUNTIME_LOCK_FILE = DATA_DIR / "runtime" / "bot.lock"
PROMPT_DIR        = BASE_DIR / "shared" / "prompts"

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

# ── 관리 웹(web_admin 기능) ───────────────────────────
# 봇 프로세스에 내장되는 관리용 웹 대시보드. FEATURES_ENABLED의 web_admin
# 키로 켜고 끄며, 봇을 제어하므로 WEB_ADMIN_PASSWORD를 지정해야만 기동한다.
WEB_ADMIN_HOST = "127.0.0.1"
WEB_ADMIN_PORT = 8787
WEB_ADMIN_USER = os.environ.get("WEB_ADMIN_USER", "admin")
WEB_ADMIN_PASSWORD = os.environ.get("WEB_ADMIN_PASSWORD", "")

SENT_NEWS_RETENTION_DAYS = 7
TELEGRAM_MESSAGE_LIMIT = 4096
# 소스 하나가 한 주기에 **번역**할 새 기사 수. Neurons를 쓰는 건 이 값이다
# (소스 6곳 기준 주기당 최대 24회 호출).
#
# 하루 번역량은 "주기 수 × 이 값"과 소스의 실제 발행량 중 작은 쪽이다. 20분
# 주기에서는 상한이 발행량보다 훨씬 커서 사실상 발행되는 대로 다 번역했다.
# 60분 주기 × 주간 17주기 × 소스 6곳 × 4건 = 하루 408건으로, 이제 상한이
# 먼저 걸린다 — 그 안에서 무엇을 고를지가 사전선별과 재탕 차단의 몫이다.
NEWS_GLOBAL_LIMIT = 4
# 번역한 기사 중 소스 하나가 실제로 **송출**할 건수. impact가 높은 순으로
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

# 번역 결과가 품질 검사에 걸린 기사는 버리고 다음 후보로 넘어간다. 그 기사는

# ── 3시간 시장상황 보고서 ────────────────────────────
# 매시간 원문만 수집하고 UTC +9 00·03·06…시에 시장별로 한 번씩 LLM을 불러
# 지난 구간의 공통 테마·상충 신호·다음 관찰 포인트를 보고한다. 기사별 번역은
# 예약 실행하지 않는다 — 호출량은 기사 수가 아니라 보고서당 시장 수에 비례한다.
NEWS_COLLECTION_INTERVAL_MINUTES = 60
NEWS_REPORT_INTERVAL_HOURS = 3
NEWS_REPORT_PROMPT_FILE = PROMPT_DIR / "news_report_ko.txt"
NEWS_REPORT_TIMEOUT = 180
NEWS_REPORT_NUM_PREDICT = 2048
# 큐에 담는 상한. 수집은 Neurons를 쓰지 않으므로 한 시간의 번역 상한보다
# 넉넉히 잡아 3시간 보고서가 반복되는 테마를 판단할 폭을 남긴다.
NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT = 12
NEWS_REPORT_QUEUE_MAX_ITEMS = 600
# 시장 하나의 보고서에 넣을 헤드라인 수와, 근거로 뽑아 보여줄 건수.
# 헤드라인 120건이면 입력이 약 6,000토큰이라 출력 2,048을 더해도 컨텍스트
# 32,768의 25% 선이다. 올릴 때는 이 계산을 다시 한다.
NEWS_REPORT_MAX_HEADLINES = 120
NEWS_REPORT_MAX_HIGHLIGHTS = 8
# 근거 기사 수는 그 시장이 그 구간에 실제로 모은 기사 수에 비례시킨다.
# 고정 8은 수집이 얇은 시장에서 무리한 요구가 된다 — 실측(2026-09-02 한국)에서
# 11건을 주고 8건을 고르라고 했다. 전체의 73%는 선별이 아니라 목록 복사이고,
# 채울 것이 모자라면 모델이 없는 것을 만든다(title 누락, 같은 index 반복).
# 11건→3, 20건→5, 32건 이상→8. 큰 시장은 지금과 같다.
NEWS_REPORT_HIGHLIGHT_RATIO = 0.25
NEWS_REPORT_MIN_HIGHLIGHTS = 3

# ── 번역 전 로컬 뉴스 사건 메모리·사전선별 ───────────
# shadow는 점수·후보·LLM 결과만 축적하고 현재 최신순 번역 순서를 바꾸지 않는다.
# 최소 일주일의 정책 비교가 끝난 뒤에만 active로 올린다. 승격 절차는
# docs/server-ops.md 7절을 따른다.
NEWS_PREFILTER_MODE = "shadow"

NEWS_PREFILTER_EVENT_WINDOW_HOURS = 72
NEWS_PREFILTER_MAX_EVENTS = 5000
# 라벨은 번역된 기사에만 붙어 하루 2,600건 남짓이고, 학습 샘플을 메모리에
# 들고 있는 비용이 여기에 비례한다(실측: 5,184건 = 29MB → 7일 약 100MB).
# 늘리기 전에 1GB 인스턴스의 여유를 다시 잰다.
NEWS_PREFILTER_OBSERVATION_RETENTION_DAYS = 7
NEWS_PREFILTER_SIMILARITY_THRESHOLD = 0.74
# active에서 번역 슬롯 하나를 임의 깊이 기사에 배정해 선택 편향을 줄인다.
# 총 번역 건수는 NEWS_GLOBAL_LIMIT 그대로라 추가 Neurons는 쓰지 않는다.
NEWS_PREFILTER_EXPLORATION_SLOTS = 1
# 같은 사건을 이미 번역했으면 이 시간 동안은 다른 기사로 다시 번역하지 않는다.
# 사건 창(72시간)보다 짧게 둔다 — 같은 사건이 사흘 내내 새 숫자를 달고
# 이어지는 경우가 있어, 재탕은 막되 후속 보도까지 막지는 않는 길이다.
NEWS_PREFILTER_TRANSLATED_EVENT_COOLDOWN_HOURS = 24

# Terraform 기본 bundle(micro_3_0: 2 vCPU, vCPU당 baseline 10%)에서 평시 봇
# 프로세스는 전체 vCPU 용량의 9%만 쓰는 것을 목표로 한다. 매 보정 주기마다
# 직전 주기의 필수 foreground CPU를 먼저 빼고 남은 몫만 보정에 배정한다.
# 리서치·3시간 시장상황 보고서·시장 컨센서스는 burst_phase로 이 제한에서 제외하며,
# 그 구간에는 보정을 멈춰 모아 둔 버스트 크레딧을 사용자 작업에 우선 쓴다.
NEWS_PREFILTER_LIGHTSAIL_VCPUS = 2
NEWS_PREFILTER_TARGET_CPU_UTILIZATION = 0.09
# 9% × 2 vCPU × 24시간 = 4.32 CPU-hour. foreground는 주기별 잔여량에서
# 차감하고, 이 일일 상한은 보정 작업 자체가 그보다 더 쓰지 못하게 하는 이중
# 안전장치다.
NEWS_PREFILTER_CALIBRATION_DAILY_BUDGET_SECONDS = 15552.0
# 1분마다 최대 10.8 CPU-second(60 × 2 × 9%)를 한 코어에서 나눠 쓴다.
# 실제 조각은 직전 1분의 foreground CPU만큼 더 작아진다.
NEWS_PREFILTER_MAINTENANCE_INTERVAL_MINUTES = 1
NEWS_PREFILTER_MAINTENANCE_CHUNK_SECONDS = 2.0
NEWS_PREFILTER_MAX_LOAD_AVERAGE = 1.5


NEWS_GLOBAL_SOURCE_KEYS = ["futu", "sina", "gnews", "gnews_us", "gnews_kr"]
NEWS_RSS_FEEDS: list[tuple[str, str]] = [
    ("mk-stock", "https://www.mk.co.kr/rss/50200011/"),
]
NEWS_SOURCE_FAILURE_THRESHOLD = 3
# 주기가 60분이라 60분 쿨다운은 한 주기도 쉬지 못하고 곧바로 다시 불린다.
# 연속 실패한 소스는 두 주기를 쉬게 둔다.
NEWS_SOURCE_COOLDOWN_MINUTES = 120
# 뉴스 메시지에 감성 점수 표기 여부.
NEWS_SENTIMENT_ENABLED = True
# /view 감성 뷰 집계에 사용할 최근 신호 일수.
VIEW_LOOKBACK_DAYS = 3
# ── 시황 리서치(/research) ────────────────────────────
RESEARCH_ANALYSIS_PROMPT_FILE = PROMPT_DIR / "market_research_ko.txt"
RESEARCH_ANALYSIS_TIMEOUT = 600
RESEARCH_NEWS_MAX_ITEMS = 16
RESEARCH_NEWS_GLOBAL_LIMIT = 8
# 분석 payload에 넣을 기사 본문 길이 상한. 제목만으로는 촉매·수치를 읽을 수
# 없어 분석이 얕아지므로 본문을 함께 넣는다. 16건 × 600자에 후보 24개·정량·
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

# 정량 컨텍스트(시세·자금흐름·섹터·涨停·용호방)
QUANT_CACHE_TTL_MINUTES = 10
QUANT_SECTOR_TOP_N = 5
QUANT_FAILURE_COOLDOWN_MINUTES = 15

# 최근 뉴스 로그(마감 브리핑 요약 입력)
NEWS_LOG_RETENTION_DAYS = 30
# Source-to-market mapping for the market sentiment dashboard.  Values are
# ISO-like market keys (CN, HK, US, KR, JP, ...); an unmapped source is kept as
# "OTHER" instead of being silently mixed into another market.
NEWS_SOURCE_MARKETS = {
    "futu": "CN",
    "sina": "CN",
    "gnews_us": "US",
    "gnews_kr": "KR",
    "mk-stock": "KR",
}
# /market(cmd_market)의 기본 조회 일수와 대상 시장 집합.
MARKET_CHART_LOOKBACK_DAYS = 7
MARKET_CHART_MARKETS = frozenset({"CN", "HK", "US", "KR"})
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
# `/market` 최대 조회 범위(30일)와 맞춘다. 그보다 오래된 항목은 차트가 읽지 않는다.
MARKET_DIGEST_RETENTION_DAYS = 30
# 요청당 LLM 호출 상한. 40회 ≈ 350 Neurons.
MARKET_DIGEST_MAX_CALLS_PER_REQUEST = 40
MARKET_DIGEST_NUM_PREDICT = 512
MARKET_DIGEST_TIMEOUT = 60
# 감성 건수의 합이 입력 헤드라인 수와 크게 다르면 그 건수를 버린다(그날의
# sentiment·summary는 남긴다). 허용 오차 = max(1, ceil(헤드라인 수 × 이 비율)).
# 이 비율은 `MARKET_DIGEST_ARTICLES_PER_DAY`와 함께 봐야 한다. 모델의 세기
# 오차는 목록이 길어질수록 비례 이상으로 커진다 — 20건 시절 실측은 93일 중
# 78일이 오차 0, 최대 2였지만 35~40건에서는 5까지 벌어졌다. 상한을 40으로
# 올리면서 0.1(35건 → 허용 4)로는 정상 응답이 탈락했다.
MARKET_DIGEST_COUNT_TOLERANCE_RATIO = 0.2

# ── 현재 Polymarket 전체 웹 대시보드 ───────────────────────────────────────
# 텔레그램·봇 scheduler와 독립된 systemd one-shot이 현재 열린 event를 읽는다.
# G0(2026-08-30)에서 /events/keyset이 limit=500 요청을 100으로 잘라 221 page를
# 반환했다. timer는 UTC +9 00·03·06…21시 고정 캘린더(하루 8회)라 공개 API
# 요청은 219 x 8 = 약 1,750회/일로 3,000회 아래다. 3시간으로 둔 것은 요청
# 때문이 아니라 줄글 브리프가 이 주기를 따라가기 때문이다
# (docs/polymarket-sector-brief.md 6절).
POLYMARKET_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_PROXY_URL = os.environ.get("POLYMARKET_PROXY_URL", "").strip()
POLYMARKET_TIMEOUT = 20
POLYMARKET_WEB_DIR = DATA_DIR / "webpub" / "polymarket"
POLYMARKET_WEB_LOW_LIQUIDITY = float(
    os.environ.get("POLYMARKET_WEB_LOW_LIQUIDITY", "1000")
)
POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS = 900.0
POLYMARKET_WEB_MAX_DAILY_REQUESTS = 3000


# ── 섹터 줄글 브리프 ───────────────────────────────────────────────────────
# 경제·금융과 지정학에 한정해 컨센서스를 줄글로 정리한다. refresh가 성공한 뒤
# 별도 one-shot이 돌며, 봇 프로세스와 무관하다. 계획서는
# docs/polymarket-sector-brief.md.
POLYMARKET_BRIEF_FILE = POLYMARKET_WEB_DIR / "sector_brief.json"
# 프롬프트에 제목을 넣을 최대 event 수. 집계는 전부 반영하고 이름만 자른다.
# 대상이 1,000건을 넘어 이 상한이 실제로 걸린다 — 거래량 상위부터 채운다.
POLYMARKET_BRIEF_NAMED_LIMIT = 120
# 이 미만이면 LLM을 부르지 않고 "표본 부족"으로 비운다. 모델은 3건짜리
# 그룹에도 그럴듯한 단락을 써 주는데 그게 제일 위험하다.
# 5다 — 2026-09-01 실측에서 복합(경제·지정학)이 8건이었다. 이 상한 이하의
# 그룹은 이름 상한(120) 안에 event가 전부 들어가 모델이 완전한 정보를 본다.
# 위험한 것은 표본이 작은 것 자체가 아니라 일부만 보고 분야 전체를 단정하는
# 것이다.
POLYMARKET_BRIEF_MIN_EVENTS = 5
# 복합만 예외로 훨씬 낮게 둔다. 경제와 지정학 태그를 **동시에** 단 event만
# 들어오는 구조라 얇을 수밖에 없는데(실측 8건), 지정학을 감시 목록에 넣은
# 이유가 바로 이 교차 지점이다. 표본이 얇다고 비워 두면 그 이유가 화면에서
# 사라진다. 대신 프롬프트가 event_count를 보고 분야 전체를 단정하지 않게 한다 —
# 몇 건일 때 그 문장은 컨센서스가 아니라 그 베팅들 자체의 서술이다.
POLYMARKET_BRIEF_MIN_EVENTS_BY_GROUP = {"composite": 2}
# 이 시각(UTC +9)에는 LLM을 부르지 않고 끝낸다. refresh는 계속 돌아 확률
# 숫자는 갱신되고, 줄글만 멈춘다 — 비용의 실체는 LLM이고 API 순회는 공짜다.
# 03시만 거른다. 그 시각 글은 06시에 덮이는데 그 사이 세 시간은 읽는 사람이
# 자고 있어 읽힐 가능성이 하루 중 가장 낮다. 06시는 절대 거르지 않는다 —
# 미장이 막 끝난 직후라 하루치가 확정된 시점이고 기상 후 첫 화면이 그것이다.
# (KST 03시는 ET 14시로 장중이지만, 이 줄글은 변화 로그가 아니라 그 시점의
#  현재 상태 요약이라 한 슬롯을 걸러도 06시 글에 그대로 반영된다.)
POLYMARKET_BRIEF_QUIET_HOURS = frozenset({3})
POLYMARKET_BRIEF_PROMPT_FILE = PROMPT_DIR / "polymarket_brief_ko.txt"
POLYMARKET_BRIEF_TIMEOUT = 180
# 단락 하나라 출력이 짧다. 다만 finish_reason=length는 재시도 없이 실패이므로
# 프롬프트가 지시한 길이의 두 배 남짓을 예약해 둔다.
POLYMARKET_BRIEF_NUM_PREDICT = 900


NEWS_MARKET_BACKFILL_QUERIES = {
    "CN": "China stock market",
    "HK": "Hong Kong stock market",
    "US": "US stock market",
    "KR": "Korea stock market",
    "JP": "Japan stock market",
    "EU": "European stock market",
    "RU": "Russia stock market",
    "TW": "Taiwan stock market",
}

# 모닝/마감 브리핑과 관심종목 편입·편출 성과표(호스트 현지 시각 기준 cron)
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
