"""웹의 LLM 조립 지점.

**웹이 LLM을 부르는 곳은 여섯이다** — 예측 컨센서스 one-shot 둘(섹터 줄글 브리프,
자연어 검색용 event 주석)은 순회 뒤에 돌고, 개인 화면의 넷(자산 조언 `POST /api/portfolio/advice`,
개인 리서치 `POST /api/research/reports`, 뷰 검증 `POST /api/research/view`, 예측 질문 답변
`POST /api/forecast/ask`)은 로그인한 계정이 요청할 때만 돈다. 공개 화면 요청은 LLM을 부르지 않는다. 봇 쪽 팩토리(`services/telegram_bot/llm/factory.py`)와 모양이
닮았지만 각자 소유다 — 봇이 번역기나 리서치 분석기의 조립을 바꿔도 이 파일은
움직이지 않는다.
"""

import logging

from services.web.core.config import (
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_AI_BASE_URL,
    CLOUDFLARE_WORKER_AI_API_TOKEN,
    CLOUDFLARE_FAILURE_COOLDOWN_SECONDS,
    CLOUDFLARE_FAILURE_THRESHOLD,
    CLOUDFLARE_MAX_ATTEMPTS,
    CLOUDFLARE_MODEL,
    POLYMARKET_ANNOTATE_NUM_PREDICT,
    POLYMARKET_ANNOTATE_PROMPT_FILE,
    POLYMARKET_ANNOTATE_TIMEOUT,
    POLYMARKET_ASK_MAX_PICKS,
    POLYMARKET_ASK_NUM_PREDICT,
    POLYMARKET_ASK_PROMPT_FILE,
    POLYMARKET_ASK_TIMEOUT,
    POLYMARKET_BRIEF_NUM_PREDICT,
    POLYMARKET_BRIEF_PROMPT_FILE,
    POLYMARKET_BRIEF_TIMEOUT,
    PORTFOLIO_ADVICE_NUM_PREDICT,
    PORTFOLIO_ADVICE_PROMPT_FILE,
    PORTFOLIO_ADVICE_TIMEOUT,
    RESEARCH_MAX_NEW_ACTIONS,
    RESEARCH_NUM_PREDICT,
    RESEARCH_PROMPT_FILE,
    RESEARCH_REMOVE_RELEVANCE_THRESHOLD,
    RESEARCH_TIMEOUT,
    VIEW_CHECK_CLAIMS_NUM_PREDICT,
    VIEW_CHECK_CLAIMS_PROMPT_FILE,
    VIEW_CHECK_JUDGE_NUM_PREDICT,
    VIEW_CHECK_JUDGE_PROMPT_FILE,
    VIEW_CHECK_MODEL,
    VIEW_CHECK_POSITIONS_NUM_PREDICT,
    VIEW_CHECK_POSITIONS_PROMPT_FILE,
    VIEW_CHECK_TIMEOUT,
    require_cloudflare_credentials,
)
from services.web.llm.backends import CloudflareWorkersAIBackend, LLMBackend, ResilientBackend
from services.web.llm.market_view import MarketViewAnalyzer
from services.web.llm.polymarket_annotation import PolymarketAnnotator
from services.web.llm.polymarket_answer import PolymarketAnswerer
from services.web.llm.polymarket_brief import PolymarketBriefAnalyzer
from services.web.llm.terminology import read_prompt
from services.web.llm.viewcheck import ViewChecker

logger = logging.getLogger(__name__)


def build_backend(purpose: str, *, model: str, timeout: int) -> LLMBackend:
    """Cloudflare 백엔드를 만들고 재시도·회로 차단으로 감싼다."""
    backend = CloudflareWorkersAIBackend(
        account_id=CLOUDFLARE_ACCOUNT_ID,
        api_token=CLOUDFLARE_WORKER_AI_API_TOKEN,
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


def build_polymarket_answerer() -> PolymarketAnswerer:
    """예측 질문 답변기. 요청마다 새로 만든다 — 동시 질문끼리 백엔드의 마지막 사용량을 섞지 않게 한다."""
    require_cloudflare_credentials()
    return PolymarketAnswerer(
        backend=build_backend("polymarket_ask", model=CLOUDFLARE_MODEL, timeout=POLYMARKET_ASK_TIMEOUT),
        prompt_file=POLYMARKET_ASK_PROMPT_FILE,
        num_predict=POLYMARKET_ASK_NUM_PREDICT,
        max_picks=POLYMARKET_ASK_MAX_PICKS,
    )


def build_portfolio_advisor():
    """개인 화면의 조언 해석기. 자격증명이 없으면 ConfigurationError — 조언은 진단만 남는다."""
    # 지연 import: advisor가 llm.backends를 읽으므로 모듈 머리에서 부르면 순환한다.
    from services.web.portfolio.advisor import PortfolioAdvisor

    require_cloudflare_credentials()
    return PortfolioAdvisor(
        backend=build_backend(
            "portfolio_advice",
            model=CLOUDFLARE_MODEL,
            timeout=PORTFOLIO_ADVICE_TIMEOUT,
        ),
        prompt=read_prompt(PORTFOLIO_ADVICE_PROMPT_FILE),
        num_predict=PORTFOLIO_ADVICE_NUM_PREDICT,
    )


def build_research_analyzer() -> MarketViewAnalyzer:
    """개인 리서치 분석기. 운영자 봇 리서치(`telegram_bot/llm/factory.py`)와 같은 값이다."""
    require_cloudflare_credentials()
    return MarketViewAnalyzer(
        backend=build_backend("personal_research", model=CLOUDFLARE_MODEL, timeout=RESEARCH_TIMEOUT),
        timeout=RESEARCH_TIMEOUT,
        num_predict=RESEARCH_NUM_PREDICT,
        prompt_file=RESEARCH_PROMPT_FILE,
        max_new_actions=RESEARCH_MAX_NEW_ACTIONS,
        remove_relevance_threshold=RESEARCH_REMOVE_RELEVANCE_THRESHOLD,
    )


def build_view_checker() -> ViewChecker:
    """뷰 검증기. 검증마다 새로 만든다 — 세 단계의 사용량을 그 검증의 것으로만 모은다.

    이 기능만 `VIEW_CHECK_MODEL`(추론 모델)을 쓴다. 기본 모델은 방향 추론을 틀린다(`core/config.py`)."""
    require_cloudflare_credentials()
    return ViewChecker(
        backend=build_backend("view_check", model=VIEW_CHECK_MODEL, timeout=VIEW_CHECK_TIMEOUT),
        claims_prompt=VIEW_CHECK_CLAIMS_PROMPT_FILE,
        judge_prompt=VIEW_CHECK_JUDGE_PROMPT_FILE,
        positions_prompt=VIEW_CHECK_POSITIONS_PROMPT_FILE,
        claims_tokens=VIEW_CHECK_CLAIMS_NUM_PREDICT,
        judge_tokens=VIEW_CHECK_JUDGE_NUM_PREDICT,
        positions_tokens=VIEW_CHECK_POSITIONS_NUM_PREDICT,
    )
