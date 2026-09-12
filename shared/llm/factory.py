"""LLM 서비스 조립 지점.

번역·시황 분석·브리핑은 모두 Cloudflare Workers AI를 쓴다. 기능(feature)들은
서비스를 직접 조립하지 않고 이 팩토리를 호출한다.
"""

import logging

from shared.core.config import (
    BRIEFING_LLM_ENABLED,
    BRIEFING_NUM_PREDICT,
    BRIEFING_PROMPT_FILE,
    BRIEFING_TIMEOUT,
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_AI_BASE_URL,
    CLOUDFLARE_MODEL,
    CLOUDFLARE_API_TOKEN,
    CLOUDFLARE_FAILURE_COOLDOWN_SECONDS,
    CLOUDFLARE_FAILURE_THRESHOLD,
    CLOUDFLARE_MAX_ATTEMPTS,
    CLOUDFLARE_TRANSLATION_TIMEOUT,
    MARKET_DIGEST_COUNT_TOLERANCE_RATIO,
    MARKET_DIGEST_NUM_PREDICT,
    MARKET_DIGEST_PROMPT_FILE,
    MARKET_DIGEST_TIMEOUT,
    MARKET_ANOMALY_PROMPT_FILE,
    NEWS_REPORT_HIGHLIGHT_RATIO,
    NEWS_REPORT_MAX_HIGHLIGHTS,
    NEWS_REPORT_MIN_HIGHLIGHTS,
    NEWS_REPORT_NUM_PREDICT,
    NEWS_REPORT_PROMPT_FILE,
    NEWS_REPORT_TIMEOUT,
    POLYMARKET_BRIEF_NUM_PREDICT,
    POLYMARKET_BRIEF_PROMPT_FILE,
    POLYMARKET_BRIEF_TIMEOUT,
    PROMPT_DIR,
    RESEARCH_ANALYSIS_NUM_PREDICT,
    RESEARCH_ANALYSIS_PROMPT_FILE,
    RESEARCH_ANALYSIS_TIMEOUT,
    RESEARCH_MAX_NEW_ACTIONS,
    RESEARCH_REMOVE_RELEVANCE_THRESHOLD,
    TRANSLATION_ENABLED,
    TRANSLATION_NUM_PREDICT,
)
from shared.llm.backends import CloudflareWorkersAIBackend, LLMBackend, ResilientBackend
from shared.llm.briefing_writer import BriefingWriter
from shared.llm.market_digest import MarketDigestAnalyzer
from shared.llm.market_view import MarketViewAnalyzer
from shared.llm.news_report import NewsReportAnalyzer
from shared.llm.overnight_tone import OvernightToneAnalyzer
from shared.llm.polymarket_brief import PolymarketBriefAnalyzer
from shared.llm.translator import TranslationService

logger = logging.getLogger(__name__)


def build_backend(purpose: str, *, model: str, timeout: int) -> LLMBackend:
    """용도별 Cloudflare 백엔드를 만들고 재시도·회로 차단으로 감싼다."""
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


def build_translation_service() -> TranslationService:
    return TranslationService(
        backend=build_backend(
            "translation",
            model=CLOUDFLARE_MODEL,
            timeout=CLOUDFLARE_TRANSLATION_TIMEOUT,
        ),
        enabled=TRANSLATION_ENABLED,
        prompt_dir=PROMPT_DIR,
        num_predict=TRANSLATION_NUM_PREDICT,
    )


def build_market_view_analyzer() -> MarketViewAnalyzer:
    return MarketViewAnalyzer(
        backend=build_backend(
            "research_analysis",
            model=CLOUDFLARE_MODEL,
            # 분석은 입력이 크고 오래 걸리므로 리서치 타임아웃을 그대로 쓴다.
            timeout=RESEARCH_ANALYSIS_TIMEOUT,
        ),
        timeout=RESEARCH_ANALYSIS_TIMEOUT,
        num_predict=RESEARCH_ANALYSIS_NUM_PREDICT,
        prompt_file=RESEARCH_ANALYSIS_PROMPT_FILE,
        max_new_actions=RESEARCH_MAX_NEW_ACTIONS,
        remove_relevance_threshold=RESEARCH_REMOVE_RELEVANCE_THRESHOLD,
    )


def build_market_digest_analyzer() -> MarketDigestAnalyzer:
    return MarketDigestAnalyzer(
        backend=build_backend(
            "market_digest",
            model=CLOUDFLARE_MODEL,
            timeout=MARKET_DIGEST_TIMEOUT,
        ),
        prompt_file=MARKET_DIGEST_PROMPT_FILE,
        num_predict=MARKET_DIGEST_NUM_PREDICT,
        count_tolerance_ratio=MARKET_DIGEST_COUNT_TOLERANCE_RATIO,
    )


def build_overnight_tone_analyzer() -> OvernightToneAnalyzer:
    return OvernightToneAnalyzer(
        backend=build_backend(
            "overnight_tone",
            model=CLOUDFLARE_MODEL,
            timeout=MARKET_DIGEST_TIMEOUT,
        ),
        prompt_file=MARKET_ANOMALY_PROMPT_FILE,
        model_id=CLOUDFLARE_MODEL,
        num_predict=384,
    )


def build_news_report_analyzer() -> NewsReportAnalyzer:
    return NewsReportAnalyzer(
        backend=build_backend(
            "news_report",
            model=CLOUDFLARE_MODEL,
            timeout=NEWS_REPORT_TIMEOUT,
        ),
        prompt_file=NEWS_REPORT_PROMPT_FILE,
        num_predict=NEWS_REPORT_NUM_PREDICT,
        max_highlights=NEWS_REPORT_MAX_HIGHLIGHTS,
        min_highlights=NEWS_REPORT_MIN_HIGHLIGHTS,
        highlight_ratio=NEWS_REPORT_HIGHLIGHT_RATIO,
    )


def build_polymarket_brief_analyzer() -> PolymarketBriefAnalyzer:
    return PolymarketBriefAnalyzer(
        backend=build_backend(
            "polymarket_brief",
            model=CLOUDFLARE_MODEL,
            timeout=POLYMARKET_BRIEF_TIMEOUT,
        ),
        prompt_file=POLYMARKET_BRIEF_PROMPT_FILE,
        num_predict=POLYMARKET_BRIEF_NUM_PREDICT,
    )


def build_briefing_writer() -> BriefingWriter:
    return BriefingWriter(
        backend=build_backend(
            "briefing",
            model=CLOUDFLARE_MODEL,
            timeout=BRIEFING_TIMEOUT,
        ),
        enabled=BRIEFING_LLM_ENABLED,
        prompt_file=BRIEFING_PROMPT_FILE,
        num_predict=BRIEFING_NUM_PREDICT,
    )
