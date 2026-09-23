"""공개 웹의 LLM 조립 지점.

**공개 웹이 LLM을 부르는 곳은 폴리마켓 one-shot 둘뿐이다** — 섹터 줄글
브리프와 자연어 검색용 event 주석. 둘 다 순회 뒤에 돌고, 화면 요청은 LLM을
부르지 않는다. 봇 쪽 팩토리(`services/telegram_bot/llm/factory.py`)와 모양이
닮았지만 각자 소유다 — 봇이 번역기나 리서치 분석기의 조립을 바꿔도 이 파일은
움직이지 않는다.
"""

import logging

from services.web.core.config import (
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_AI_BASE_URL,
    CLOUDFLARE_API_TOKEN,
    CLOUDFLARE_FAILURE_COOLDOWN_SECONDS,
    CLOUDFLARE_FAILURE_THRESHOLD,
    CLOUDFLARE_MAX_ATTEMPTS,
    CLOUDFLARE_MODEL,
    POLYMARKET_ANNOTATE_NUM_PREDICT,
    POLYMARKET_ANNOTATE_PROMPT_FILE,
    POLYMARKET_ANNOTATE_TIMEOUT,
    POLYMARKET_BRIEF_NUM_PREDICT,
    POLYMARKET_BRIEF_PROMPT_FILE,
    POLYMARKET_BRIEF_TIMEOUT,
    require_cloudflare_credentials,
)
from services.web.llm.backends import CloudflareWorkersAIBackend, LLMBackend, ResilientBackend
from services.web.llm.polymarket_annotation import PolymarketAnnotator
from services.web.llm.polymarket_brief import PolymarketBriefAnalyzer

logger = logging.getLogger(__name__)


def build_backend(purpose: str, *, model: str, timeout: int) -> LLMBackend:
    """Cloudflare 백엔드를 만들고 재시도·회로 차단으로 감싼다."""
    backend = CloudflareWorkersAIBackend(
        account_id=CLOUDFLARE_ACCOUNT_ID,
        api_token=CLOUDFLARE_API_TOKEN,
        model=model,
        base_url=CLOUDFLARE_AI_BASE_URL,
        timeout=timeout,
    )
    logger.info("[LLM] purpose=%s provider=cloudflare model=%s", purpose, model)
    return ResilientBackend(
        backend=backend,
        max_attempts=CLOUDFLARE_MAX_ATTEMPTS,
        failure_threshold=CLOUDFLARE_FAILURE_THRESHOLD,
        cooldown_seconds=CLOUDFLARE_FAILURE_COOLDOWN_SECONDS,
    )


def build_polymarket_brief_analyzer() -> PolymarketBriefAnalyzer:
    require_cloudflare_credentials()
    return PolymarketBriefAnalyzer(
        backend=build_backend(
            "polymarket_brief",
            model=CLOUDFLARE_MODEL,
            timeout=POLYMARKET_BRIEF_TIMEOUT,
        ),
        prompt_file=POLYMARKET_BRIEF_PROMPT_FILE,
        num_predict=POLYMARKET_BRIEF_NUM_PREDICT,
    )


def build_polymarket_annotator() -> PolymarketAnnotator:
    require_cloudflare_credentials()
    return PolymarketAnnotator(
        backend=build_backend(
            "polymarket_annotate",
            model=CLOUDFLARE_MODEL,
            timeout=POLYMARKET_ANNOTATE_TIMEOUT,
        ),
        prompt_file=POLYMARKET_ANNOTATE_PROMPT_FILE,
        num_predict=POLYMARKET_ANNOTATE_NUM_PREDICT,
    )
