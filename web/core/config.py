"""공개 웹 프로세스의 환경 변수와 설정 상수.

**이 파일은 `web/`만 읽는다.** 텔레그램 봇에는 자기 것(`telegram_bot/core/config.py`)이
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

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

load_dotenv(BASE_DIR / ".env")


class ConfigurationError(RuntimeError):
    pass


# ── Cloudflare Workers AI ─────────────────────────────
# 줄글 브리프 하나만 쓴다. 봇 쪽 복사본과 값이 같아 보여도 각자 소유다 —
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
    """줄글 브리프를 부르기 직전에 확인한다.

    import 시점에 검사하지 않는 것이 봇 쪽과 다르다. 공개 웹 서버(`web.server`)와
    숫자 순회(`web.polymarket.refresh`)는 LLM을 쓰지 않으므로, 자격증명이 없다고
    화면과 확률 숫자까지 함께 멈출 이유가 없다. 멈춰야 하는 것은 줄글 one-shot
    하나뿐이다.
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
            f"폴리마켓 줄글 브리프에 필요한 {', '.join(missing)}이(가) .env에 비어 있습니다"
        )


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
