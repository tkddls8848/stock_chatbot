"""기능 카탈로그의 단일 조립 지점."""

from services.telegram_bot.features.briefing.feature import FEATURE as BRIEFING
from services.telegram_bot.features.instruments.feature import FEATURE as INSTRUMENTS
from services.telegram_bot.features.market_sentiment.feature import FEATURE as MARKET_SENTIMENT
from services.telegram_bot.features.news_prefilter.feature import FEATURE as NEWS_PREFILTER
from services.telegram_bot.features.news_summary.feature import FEATURE as NEWS_SUMMARY
from services.telegram_bot.features.registry import FeatureRegistry
from services.telegram_bot.features.research.feature import FEATURE as RESEARCH
from services.telegram_bot.features.sector_summary.feature import FEATURE as SECTOR_SUMMARY
from services.telegram_bot.features.shorts.feature import FEATURE as SHORTS
from services.telegram_bot.features.system_admin.feature import FEATURE as SYSTEM_ADMIN
from services.telegram_bot.features.watchlist.feature import FEATURE as WATCHLIST
from services.telegram_bot.features.web_status.feature import FEATURE as WEB_STATUS

ALL_FEATURES = (
    INSTRUMENTS,       # 종목 마스터 데이터 — 종목 DB의 기반
    SECTOR_SUMMARY,    # 시세·자금흐름·섹터 요약 컨텍스트
    WATCHLIST,         # 관심종목 관리
    NEWS_PREFILTER,    # 번역 전 로컬 뉴스 사건 메모리·사전선별
    NEWS_SUMMARY,      # 뉴스 수집·시장상황 보고서
    MARKET_SENTIMENT,  # 국가별 뉴스 감성 — 예약 갱신, 패널에서 지금 갱신
    RESEARCH,          # 시장 리서치 — 예약 실행, 패널에서 주제·지금 실행
    BRIEFING,          # 모닝·마감 브리핑
    SYSTEM_ADMIN,      # 시작·도움말·시스템 제어
    WEB_STATUS,        # 웹 산출물 갱신 상태(관리 패널)
    SHORTS,            # 쇼츠 운영(관리 패널) — 쇼츠 CLI를 하위 프로세스로
)


def build_feature_registry(enabled_keys) -> FeatureRegistry:
    return FeatureRegistry(ALL_FEATURES, enabled_keys)


__all__ = ["ALL_FEATURES", "FeatureRegistry", "build_feature_registry"]
