"""공개 웹 프로세스의 환경 변수와 설정 상수.

**이 파일은 `services/web/`만 읽는다.** 텔레그램 봇에는 자기 것(`services/telegram_bot/core/config.py`)이
따로 있고 둘은 서로를 import하지 않는다. 예전에는 `shared/core/config.py` 한 벌을
둘이 같이 읽었는데, 그 파일이 최상단에서 `os.environ["TELEGRAM_BOT_TOKEN"]`을
읽는 바람에 **텔레그램 토큰이 없으면 공개 웹도 기동하지 못했다.** 한쪽 사정이
다른 쪽 장애가 되는 자리가 정확히 여기였다.

설정 저장 방침은 봇과 같다. `.env`에는 비밀값·자격증명만 두고, 나머지 튜닝값은
이 모듈의 리터럴 상수로 두어 변경 이력이 git에 남게 한다. 예외는
`CLOUDFLARE_MODEL` 하나로, Cloudflare가 모델을 폐기·개명하면 코드 배포 없이
즉시 고칠 수 있어야 한다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[3]
PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

load_dotenv(BASE_DIR / ".env")

# 공유 저장소(NAS). 봇·one-shot·쇼츠와 같은 경로를 각자 설정으로 읽는다
# (`code_guide.md`의 「공유 저장소」). 봇 설정을 import하지 않는다.
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "").strip() or BASE_DIR / "storage")
# 공개 라우트가 내보내는 유일한 폴더.
PUBLIC_DIR = STORAGE_DIR / "public"
# 개인 화면(/portfolio)의 자산·관심종목·조언. 공개 라우트에 연결하지 않는다.
PORTFOLIO_DIR = STORAGE_DIR / "portfolio"


class ConfigurationError(RuntimeError):
    pass


# ── Cloudflare Workers AI ─────────────────────────────
# 폴리마켓 one-shot 둘(줄글 브리프·검색 주석)만 쓴다. 봇 쪽 복사본과 값이 같아 보여도 각자 소유다 —
# 한쪽이 모델이나 타임아웃을 바꿔도 다른 쪽 프로세스는 흔들리지 않는다.
# API 토큰은 .env에만 두고 커밋하지 않는다. 로그·예외에도 남기지 않는다.
CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
CLOUDFLARE_AI_BASE_URL = "https://api.cloudflare.com/client/v4"
CLOUDFLARE_MODEL = os.environ.get(
    "CLOUDFLARE_MODEL", "@cf/qwen/qwen3-30b-a3b-fp8"
).strip()
CLOUDFLARE_MAX_ATTEMPTS = 2
CLOUDFLARE_FAILURE_THRESHOLD = 3
CLOUDFLARE_FAILURE_COOLDOWN_SECONDS = 300


def require_cloudflare_credentials() -> None:
    """LLM one-shot(줄글 브리프·검색 주석)을 부르기 직전에 확인한다.

    import 시점에 검사하지 않는 것이 봇 쪽과 다르다. 공개 웹 서버(`services.web.server`)와
    숫자 순회(`services.web.polymarket.refresh`)는 LLM을 쓰지 않으므로, 자격증명이 없다고
    화면과 확률 숫자까지 함께 멈출 이유가 없다. 멈춰야 하는 것은 LLM을 부르는
    one-shot뿐이다.
    """
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
            f"웹 LLM 기능(예측 줄글·검색 주석·자산 조언)에 필요한 {', '.join(missing)}이(가) .env에 비어 있습니다"
        )


# ── 현재 Polymarket 전체 웹 대시보드 ───────────────────────────────────────
# 텔레그램·봇 scheduler와 독립된 systemd one-shot이 현재 열린 event를 읽는다.
# G0(2026-08-30)에서 /events/keyset이 limit=500 요청을 100으로 잘라 221 page를
# 반환했다. timer는 UTC +9 00·04·08·12·16·20시 고정 캘린더(하루 6회)라 공개 API
# 요청은 219 x 6 = 약 1,300회/일로 3,000회 아래다. 주기는 요청 수가 아니라 줄글
# 브리프가 이 주기를 따라가는 비용으로 정한다(2026-09-24 3시간 → 4시간)
# (docs/polymarket-sector-brief.md 6절).
POLYMARKET_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_PROXY_URL = os.environ.get("POLYMARKET_PROXY_URL", "").strip()
POLYMARKET_TIMEOUT = 20
POLYMARKET_WEB_DIR = PUBLIC_DIR / "polymarket"
POLYMARKET_WEB_LOW_LIQUIDITY = float(
    os.environ.get("POLYMARKET_WEB_LOW_LIQUIDITY", "1000")
)
POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS = 900.0
POLYMARKET_WEB_MAX_DAILY_REQUESTS = 3000
# 마감이 이 시간 안에 닥친 event는 **확률 순위에서 뺀다.** 오늘 기온·5분짜리 코인
# 가격 방향·오늘 밤 경기처럼 결과가 곧 확정되는 시장은 확률이 0·1로 수렴하는 것이
# 당연해서, 그 수렴을 "가장 굳은 예측"이라고 부르면 순위가 마감 시계를 재는 표가
# 된다. 그날 트렌드(`trending.py`)가 후보를 고를 때와 화면 순위 목록
# (`repository.summary`)이 같은 값을 읽는다 — 두 곳이 다른 기준을 쓰면 같은 화면
# 안에서 어떤 event는 순위에 있고 조명에는 없다(실측: 트렌드 후보 400건 중 155건).
# 전체 목록·검색·필터에서는 빼지 않는다. 지금 열려 있는 질문은 다 보여 준다.
POLYMARKET_MIN_HOURS_TO_END = 72



# ── 그날 트렌드 이슈 집중 조명 ─────────────────────────────────────────────
# 화면은 "지금"만 보지만, **무엇이 오늘 움직였는가**는 지금 값 하나로 답할 수
# 없다. 그래서 이 one-shot만 자기 스냅숏을 자기 파일에 들고 그날치 이동을 잰다.
# generation 이력을 늘리지 않는 것이 요점이다 — detail shard가 generation 하나에
# 100 MiB를 넘어(dashboard/storage.py) 과거 generation을 남기는 방식은 디스크가
# 먼저 찬다. 여기 남는 것은 후보 event의 {확률, 1위, 거래량} 뿐이다.
POLYMARKET_TRENDING_FILE = POLYMARKET_WEB_DIR / "trending.json"
# 이동을 추적할 후보 수. 거래량 상위부터 채운다. 열린 event 전부(실측 21,872)를
# 담으면 스냅숏 두 벌이 매 주기 수 MiB가 되는데, 거래가 없는 event의 가격 이동은
# 호가 한 건에도 흔들려 트렌드가 아니라 잡음이다.
POLYMARKET_TRENDING_CANDIDATE_LIMIT = 400
# 2026-09-25 16시 사본 19,054건 중 24h 25K 이상은 194건, 50K 이상은 120건.
# 기존 제외 조건까지 적용하면 후보 244→32건. 25K는 Kanye(32,147달러)와
# 악수 시간(27,929달러)을 남겨 50K로 둔다. 작은 규모의 큰 이동을 먼저 거른다.
POLYMARKET_TRENDING_MIN_VOLUME = 50000.0
# 같은 사본에서 24h 50K + 잔액 25K는 후보 30건·2pp 이상 조명 4건을 남긴다.
# AI 개명 질문(24h 58,227달러·잔액 13,739달러)은 규모 하한만으로 남으므로
# 잔액도 확인한다. log 규모 가중만으로는 상위 순서가 바뀌지 않아 도입하지 않는다.
POLYMARKET_TRENDING_MIN_LIQUIDITY = 25000.0
# 마감이 임박한 event를 후보에서 빼는 기준은 위 `POLYMARKET_MIN_HOURS_TO_END`다.
# 화면 순위와 같은 값을 읽는다.
# 이 분야는 후보로 보지 않는다. 경기·날씨는 거래량이 커도 시장 컨센서스가 아니다.
# 복합 분야는 구성 분야 중 하나라도 여기 걸리면 뺀다.
POLYMARKET_TRENDING_EXCLUDED_CATEGORIES = frozenset({"sports", "weather_climate"})
# 화면에 조명할 건수. 한 화면에서 훑고 끝낼 수 있는 분량으로 둔다.
POLYMARKET_TRENDING_SPOTLIGHT_LIMIT = 10
# 이 아래 이동은 조명하지 않는다. 한 주기(4시간)에 2pp 미만은 컨센서스가 바뀐 것이 아니라
# 같은 자리에서 흔들린 것이다.
POLYMARKET_TRENDING_MOVE_FLOOR = 0.02
# 신규 진입·거래량 급증 목록의 길이. 조명이 주인공이고 이 둘은 곁들이다.
POLYMARKET_TRENDING_LIST_LIMIT = 5

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
# 04시만 거른다. 그 시각 글은 08시에 덮이는데 그 사이 네 시간은 읽는 사람이
# 자고 있어 읽힐 가능성이 하루 중 가장 낮다. 08시는 절대 거르지 않는다 —
# 미장 마감(KST 05~06시) 뒤 하루치가 확정된 첫 슬롯이고 기상 후 첫 화면이 그것이다.
# (KST 04시는 ET 15시로 장중이지만, 이 줄글은 변화 로그가 아니라 그 시점의
#  현재 상태 요약이라 한 슬롯을 걸러도 08시 글에 그대로 반영된다.)
# 값은 timer 슬롯 중 하나여야 한다 — 슬롯에 없는 시각은 영영 오지 않아 조용히
# 아무것도 거르지 않는다(주기를 3→4시간으로 바꿀 때 {3}이 그렇게 될 뻔했다).
POLYMARKET_BRIEF_QUIET_HOURS = frozenset({4})
POLYMARKET_BRIEF_PROMPT_FILE = PROMPT_DIR / "polymarket_brief_ko.txt"
POLYMARKET_BRIEF_TIMEOUT = 180
# 단락 하나라 출력이 짧다. 다만 finish_reason=length는 재시도 없이 실패이므로
# 프롬프트가 지시한 길이의 두 배 남짓을 예약해 둔다.
POLYMARKET_BRIEF_NUM_PREDICT = 900

# ── 자연어 검색용 event 주석 ────────────────────────────────────────────────
# 순회가 성공한 뒤 별도 one-shot이 event마다 한국어 요약·검색 키워드를 달아
# 둔다. 검색하는 순간에는 LLM을 부르지 않는다. 계획서는
# docs/polymarket-nl-search.md.
#
# current.json에 넣지 않는다 — 건당 약 250 B × 21,872건이면 16 MiB 상한의
# 3분의 1이다. 별도 파일로 두고 repository가 따로 읽는다.
POLYMARKET_SEARCH_INDEX_FILE = POLYMARKET_WEB_DIR / "search_index.json"
# 최근 24시간 호출 표본. refresh의 status.json처럼 예산을 스스로 지키는 데 쓴다.
POLYMARKET_ANNOTATE_STATUS_FILE = POLYMARKET_WEB_DIR / "annotate_status.json"
POLYMARKET_ANNOTATE_PROMPT_FILE = PROMPT_DIR / "polymarket_annotate_ko.txt"
# 한 호출에 묶는 event 수. 출력이 건당 100토큰 안팎이라 25건이면 2,500토큰
# 남짓이다. 더 묶으면 max_tokens 절단 한 번에 버리는 건수가 커진다.
POLYMARKET_ANNOTATE_BATCH_SIZE = 25
POLYMARKET_ANNOTATE_NUM_PREDICT = 4096
POLYMARKET_ANNOTATE_TIMEOUT = 180
# 한 실행의 호출 상한. 백필이 하루 예산을 첫 주기에 몰아 쓰지 않고 4시간
# 주기마다 나눠 쓰게 하고, 유닛의 TimeoutStartSec 안에 끝나게 한다.
# 주기를 3→4시간으로 늘리며(하루 8→6회) 20% 올렸다(8→10). 하루 처리량 64→60배치로
# 거의 같고, 하루 Neurons 상한은 그대로라 비용은 늘지 않는다. TimeoutStartSec 안에
# 끝나는지는 배포 뒤 annotate_status.json의 실행 시간으로 확인한다.
POLYMARKET_ANNOTATE_MAX_BATCHES_PER_RUN = 10
# 최근 24시간 Neurons 상한. 무료 한도(하루 10,000)를 봇·섹터 줄글(하루
# 1,500~2,500)과 함께 쓴다. 첫 백필(약 5만) 동안 올릴지는 봇 사용량을 보고
# 여기서 정한다 — 튜닝값이라 .env가 아니라 이력이 남는 상수로 둔다.
POLYMARKET_ANNOTATE_MAX_DAILY_NEURONS = 4000.0
# 모델에 넘기는 설명(description) 길이. 제목만으로는 무엇을 거는지 모호한
# event가 있어 앞부분만 붙인다. 뒷부분은 대개 판정 규칙의 세부다.
POLYMARKET_ANNOTATE_DESCRIPTION_CHARS = 300


# ── 개인 화면: 전체 자산 포트폴리오 어드바이저(/portfolio) ────────────────────
# 규칙은 code_guide.md의 「개인 화면」. 공개 화면과 섞지 않는다.
# 비밀번호 하나로 여는 간단한 잠금이다. 비어 있으면 개인 화면 전체가 503으로
# 닫힌다 — 빈 비밀번호로 열리지 않는다.
PORTFOLIO_PASSWORD = os.environ.get("PORTFOLIO_PASSWORD", "").strip()
PORTFOLIO_SESSION_COOKIE = "nunchi_portfolio"
# 잠금 해제 쿠키의 수명. 쿠키 값은 비밀번호에서 만든 HMAC이라 서버에 세션 저장소가
# 없고, 비밀번호를 바꾸면 기존 쿠키가 모두 풀린다.
PORTFOLIO_SESSION_MAX_AGE_SECONDS = 14 * 24 * 3600
# 비밀번호 추측을 늦춘다. 이 창 안에서 이만큼 틀리면 잠시 429로 막는다.
PORTFOLIO_LOGIN_MAX_FAILURES = 5
PORTFOLIO_LOGIN_WINDOW_SECONDS = 600
PORTFOLIO_ASSETS_FILE = PORTFOLIO_DIR / "assets.json"
# 봇과 같이 쓰는 파일(봇은 리서치 자동 적용만 쓴다). 잠금 파일 이름은 봇과 같아야 한다.
PORTFOLIO_WATCHLIST_FILE = PORTFOLIO_DIR / "watchlist.json"
PORTFOLIO_ADVICE_DIR = PORTFOLIO_DIR / "advice"
PORTFOLIO_MAX_ASSETS = 200
PORTFOLIO_MAX_WATCHLIST = 200

# 조언은 요청할 때만 만든다. 반복 클릭 비용을 하루 상한으로 막는다(최근 24시간이
# 아니라 한국 시간 달력 하루).
PORTFOLIO_ADVICE_MAX_DAILY = 10
PORTFOLIO_ADVICE_HISTORY_LIMIT = 30
PORTFOLIO_ADVICE_PROMPT_FILE = PROMPT_DIR / "portfolio_advice_ko.txt"
PORTFOLIO_ADVICE_TIMEOUT = 180
PORTFOLIO_ADVICE_NUM_PREDICT = 2048

# 외부 시장 자료. 키가 비면 그 항목만 "자료 없음"으로 두고 나머지로 진행한다.
FSS_API_KEY = os.environ.get("FSS_API_KEY", "").strip()      # 금융감독원 금융상품통합비교공시
ECOS_API_KEY = os.environ.get("ECOS_API_KEY", "").strip()    # 한국은행 ECOS
MOLIT_API_KEY = os.environ.get("MOLIT_API_KEY", "").strip()  # 국토교통부 실거래가(공공데이터포털)
FSS_BASE_URL = "https://finlife.fss.or.kr/finlifeapi"
ECOS_BASE_URL = "https://ecos.bok.or.kr/api"
MOLIT_APT_TRADE_URL = (
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
)
MARKET_DATA_TIMEOUT = 15
# 실거래가 중앙값을 낼 때 거슬러 보는 달 수. 한 달만 보면 거래가 몇 건 없는 동이 많다.
MOLIT_LOOKBACK_MONTHS = 3

# 규칙 진단의 문턱. 숫자는 여기서 만들고 LLM은 해석만 한다.
PORTFOLIO_CLASS_CONCENTRATION = 0.6    # 한 자산군이 이 비중을 넘으면 편중
PORTFOLIO_SINGLE_CONCENTRATION = 0.3   # 한 자산이 이 비중을 넘으면 편중
PORTFOLIO_MATURITY_WINDOW_DAYS = 90    # 이 안에 만기가 오면 알린다
PORTFOLIO_RATE_GAP_PP = 0.5            # 보유 예적금 금리가 시중 최고보다 이만큼 낮으면 알린다
PORTFOLIO_LTV_WARNING = 0.6            # 부동산 대출 비율
PORTFOLIO_MIN_LIQUID_SHARE = 0.1       # 예적금 비중이 이보다 낮으면 유동성 부족
