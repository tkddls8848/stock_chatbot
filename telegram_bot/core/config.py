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
        "sector_summary",
        "watchlist",
        "news_prefilter",
        "news_summary",
        "market_sentiment",
        "research",
        "briefing",
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
# (news/, watchlist/, instruments/, research/, runtime/)
DATA_DIR          = BASE_DIR / "data"
SENT_IDS_FILE     = DATA_DIR / "news" / "sent_ids.json"
NEWS_LOG_FILE     = DATA_DIR / "news" / "news_log.json"
NEWS_REPORT_QUEUE_FILE = DATA_DIR / "news" / "news_report_queue.json"
NEWS_REPORT_MEMORY_FILE = DATA_DIR / "news" / "news_report_memory.json"
WATCHLIST_FILE    = DATA_DIR / "watchlist" / "watchlist.json"
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

# ── 관리 웹(web_admin 기능) ───────────────────────────
# 봇 프로세스에 내장되는 관리용 웹 대시보드. FEATURES_ENABLED의 web_admin
# 키로 켜고 끄며, 봇을 제어하므로 WEB_ADMIN_PASSWORD를 지정해야만 기동한다.
WEB_ADMIN_HOST = "127.0.0.1"
WEB_ADMIN_PORT = 8787
WEB_ADMIN_USER = os.environ.get("WEB_ADMIN_USER", "admin")
WEB_ADMIN_PASSWORD = os.environ.get("WEB_ADMIN_PASSWORD", "")

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
# 매시간 원문만 수집하고 UTC +9 00·03·06…시에 **발행할지부터 판정한다.**
# 3시간은 검토 주기이고 발행 주기가 아니다 — 재료가 얇거나 직전 보고서의
# 판단이 그대로인 구간에 한 편을 억지로 쓰게 하면 같은 국면을 다른 문장으로
# 반복하게 되고, 그 반복이 보고서를 기계적으로 만든다. 보류한 시장의 기사는
# 큐에 남아 다음 구간에 더 두꺼운 재료로 다시 평가된다.
# 기사별 번역은 예약 실행하지 않는다 — 호출량은 기사 수가 아니라 발행·검토한
# 시장 수에 비례하고, 1차 게이트에 걸린 시장은 LLM을 부르지도 않는다.
NEWS_COLLECTION_INTERVAL_MINUTES = 60
NEWS_REPORT_INTERVAL_HOURS = 3
NEWS_REPORT_PROMPT_FILE = PROMPT_DIR / "news_report_ko.txt"
NEWS_REPORT_TIMEOUT = 180
# 출력 예약. **2048은 상시로 모자랐다** — 2026-09-17 하루에만 06시 CN·US·KR과
# 03시 US가 정확히 2048에서 잘려(`finish_reason=length`) 보고서가 원문 제목
# 나열로 떨어졌다. 입력은 2,832~3,601 토큰으로 작았으므로 원인은 입력이 아니다.
# 한국어는 토큰이 비싸서 analysis 400~500자에 번역 제목 8건·evaluations까지
# 얹으면 2048에 닿는다. 컨텍스트 32,768에 견줘 여유가 크고, 무료 한도 대비
# 소비도 하루 1,349/10,000(실측 2026-09-17)이라 올릴 자리가 있다.
NEWS_REPORT_NUM_PREDICT = 4096
# 본문을 450~650자로 늘리면서 함께 올렸다. 650자는 한국어 토큰으로 약 1,000이고
# 근거 기사 8건(제목 80자·부가 필드)이 약 1,300, evaluations 10건이 약 150,
# 발행 판정 두 필드가 약 100이라 합이 2,600 선이다. 3584에 남는 여유가
# 900토큰뿐이라 highlights가 상한까지 찬 구간에서 다시 length에 닿는다.
# 큐에 담는 상한. 수집은 LLM을 부르지 않으며 보고서가 여러 사건을 비교할
# 폭을 확보한다. 사전선별도 이 상한으로 점수·탐색 슬롯을 배정한다.
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
# `sina`는 뺐다(2026-09-21 실측, 09-22 재확인). zhibo.sina.com.cn(49.7.36.230)이
# 이 서버에서 닿지 않는다 — DNS는 풀리는데 SYN이 China Telecom 국제 백본
# (202.97.x) 안쪽에서 조용히 버려진다. ICMP도 전 포트도 무응답이라 사이트의
# 지역 차단이 아니라 경로 차단이고, 서버를 옮겨도 중국 밖이면 같다.
# 거절(RST)이 아니라 드롭이라 connect 한 번이 tcp_syn_retries=6 만큼 약 127초를
# 물고, retry_on_network의 재시도 3회가 곱해져 수집 워커 하나가 6분 넘게 잡혔다.
# 부팅 즉시 도는 수집(next_run_time=now())이 그 창을 만들어, 그 안에 SIGTERM이
# 오면 non-daemon 스레드를 조인하지 못해 종료가 90초 뒤 SIGKILL로 끝났다.
# 중화권 1차 소스 자리는 `cls`가 맡는다. 경로가 열리면 이 목록에 다시 넣기만
# 하면 된다 — 소스 정의는 news/registry.py 카탈로그에 그대로 있다.
NEWS_GLOBAL_SOURCE_KEYS = ["futu", "cls", "gnews", "gnews_us", "gnews_kr"]
NEWS_RSS_FEEDS: list[tuple[str, str]] = [
    ("mk-stock", "https://www.mk.co.kr/rss/50200011/"),
    ("yonhap-economy", "https://www.yna.co.kr/rss/economy.xml"),
    ("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml"),
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
    "futu": "CN",
    "sina": "CN",
    "cls": "CN",
    "gnews_us": "US",
    "gnews_kr": "KR",
    "mk-stock": "KR",
    "yonhap-economy": "KR",
    "fed-press": "US",
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
