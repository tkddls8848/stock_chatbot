"""기능 카탈로그의 단일 조립 지점."""

from telegram_bot.features.briefing.feature import FEATURE as BRIEFING
from telegram_bot.features.instruments.feature import FEATURE as INSTRUMENTS
from telegram_bot.features.market_sentiment.feature import FEATURE as MARKET_SENTIMENT
from telegram_bot.features.news.feature import FEATURE as NEWS
from telegram_bot.features.news_prefilter.feature import FEATURE as NEWS_PREFILTER
from telegram_bot.features.quant.feature import FEATURE as QUANT
from telegram_bot.features.registry import FeatureRegistry
from telegram_bot.features.research.feature import FEATURE as RESEARCH
from telegram_bot.features.signal_scoring.feature import FEATURE as SIGNAL_SCORING
from telegram_bot.features.system_admin.feature import FEATURE as SYSTEM_ADMIN
from telegram_bot.features.watchlist.feature import FEATURE as WATCHLIST
from telegram_bot.features.web_admin.feature import FEATURE as WEB_ADMIN

ALL_FEATURES = (
    INSTRUMENTS,       # 종목 마스터 데이터 — 종목 DB의 기반
    QUANT,             # 시세·자금흐름·섹터 정량 컨텍스트
    WATCHLIST,         # 관심종목 관리
    NEWS_PREFILTER,    # 번역 전 로컬 뉴스 사건 메모리·사전선별
    NEWS,              # 뉴스 수집·번역·전송
    MARKET_SENTIMENT,  # 국가별 뉴스 감성(폴리마켓 컨센서스·이상탐지 포함)
    RESEARCH,          # 시장 리서치
    BRIEFING,          # 모닝·마감 브리핑
    SIGNAL_SCORING,    # 종목 감성 뷰
    SYSTEM_ADMIN,      # 시작·도움말·시스템 제어
    WEB_ADMIN,         # 관리 웹 대시보드
)


def build_feature_registry(enabled_keys) -> FeatureRegistry:
    return FeatureRegistry(ALL_FEATURES, enabled_keys)


__all__ = ["ALL_FEATURES", "FeatureRegistry", "build_feature_registry"]
