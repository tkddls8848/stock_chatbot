"""전역 뉴스 소스 레지스트리.

설정된 우선순위 목록으로 소스를 관리하고, 연속 실패한 소스는 일정 시간
쿨다운시켜 자동으로 건너뛴다. 쿨다운이 끝나면 다음 주기에 자동 재시도한다.
파이프라인과 리서치 수집이 같은 레지스트리를 공유한다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from telegram_bot.core.clock import now
from functools import partial
from typing import Callable

from telegram_bot.news.sources import (
    GlobalArticle,
    fetch_cls_articles,
    fetch_futu_articles,
    fetch_google_news_global_articles,
    fetch_google_news_kr_stock_articles,
    fetch_google_news_us_stock_articles,
    fetch_rss_articles,
    fetch_sina_articles,
)

logger = logging.getLogger(__name__)


@dataclass
class SourceSpec:
    key: str
    label: str
    fetch: Callable[[], list[GlobalArticle]]
    # 소스가 대표하는 시장(CN/US/KR...). 빈 값은 여러 시장이 섞인 소스라는 뜻이며,
    # 이때 시장 구분은 기사별 extra["market"]에서 얻는다.
    market: str = ""


@dataclass
class _SourceHealth:
    consecutive_failures: int = 0
    cooldown_until: datetime | None = None
    last_error: str = ""


# (label, fetch, market)
_BUILTIN_SPECS: dict[str, tuple[str, Callable[[], list[GlobalArticle]], str]] = {
    "futu": ("푸투니우니우(富途牛牛)", fetch_futu_articles, "CN"),
    "sina": ("신랑재경(新浪财经)", fetch_sina_articles, "CN"),
    "cls": ("차이롄서 전보(财联社 电报)", fetch_cls_articles, "CN"),
}

_BUILTIN_SPECS["gnews"] = (
    "Google News Global",
    fetch_google_news_global_articles,
    "",  # 기사별 extra["market"]로 구분되는 혼합 소스
)
_BUILTIN_SPECS["gnews_us"] = (
    "미국 증시 뉴스",
    fetch_google_news_us_stock_articles,
    "US",
)
_BUILTIN_SPECS["gnews_kr"] = (
    "한국 증시 뉴스",
    fetch_google_news_kr_stock_articles,
    "KR",
)


def build_source_specs(
    source_keys: list[str],
    rss_feeds: list[tuple[str, str]],
    source_markets: dict[str, str] | None = None,
) -> list[SourceSpec]:
    """설정 문자열에서 SourceSpec 목록을 만든다. 순서가 곧 우선순위다.

    source_markets(NEWS_SOURCE_MARKETS)는 소스 키 → 시장 매핑으로, RSS 피드처럼
    빌트인 시장 태그가 없는 소스에 시장을 부여하거나 기본 태그를 덮어쓴다.
    """
    markets = source_markets or {}

    def resolve_market(key: str, default: str = "") -> str:
        return str(markets.get(key.lower()) or default).upper()

    specs: list[SourceSpec] = []
    seen: set[str] = set()
    for key in source_keys:
        key = key.strip().lower()
        if not key or key in seen:
            continue
        builtin = _BUILTIN_SPECS.get(key)
        if builtin is None:
            logger.warning("[NEWS] 알 수 없는 전역 뉴스 소스 무시: %s", key)
            continue
        label, fetch, market = builtin
        specs.append(
            SourceSpec(
                key=key,
                label=label,
                fetch=fetch,
                market=resolve_market(key, market),
            )
        )
        seen.add(key)

    for label, url in rss_feeds:
        key = f"rss:{label}"
        if key in seen:
            continue
        specs.append(
            SourceSpec(
                key=key,
                label=label,
                fetch=partial(fetch_rss_articles, url, label),
                market=resolve_market(key) or resolve_market(label),
            )
        )
        seen.add(key)
    return specs


class NewsSourceRegistry:
    def __init__(
        self,
        specs: list[SourceSpec],
        failure_threshold: int = 3,
        cooldown_minutes: int = 60,
    ):
        self._specs = specs
        self._failure_threshold = max(1, failure_threshold)
        self._cooldown = timedelta(minutes=max(1, cooldown_minutes))
        self._health: dict[str, _SourceHealth] = {spec.key: _SourceHealth() for spec in specs}

    def active_specs(self) -> list[SourceSpec]:
        """쿨다운 중이 아닌 소스를 우선순위 순서로 반환한다."""
        current = now()
        active = []
        for spec in self._specs:
            health = self._health[spec.key]
            if health.cooldown_until and current < health.cooldown_until:
                continue
            active.append(spec)
        return active

    def record_success(self, key: str) -> None:
        health = self._health.get(key)
        if health is None:
            return
        if health.consecutive_failures >= self._failure_threshold:
            logger.info("[NEWS] 소스 복구됨: %s", key)
        health.consecutive_failures = 0
        health.cooldown_until = None
        health.last_error = ""

    def record_failure(self, key: str, error: str) -> None:
        health = self._health.get(key)
        if health is None:
            return
        health.consecutive_failures += 1
        health.last_error = error[:200]
        if health.consecutive_failures >= self._failure_threshold:
            health.cooldown_until = now() + self._cooldown
            logger.warning(
                "[NEWS] 소스 %s 연속 %d회 실패 → %s까지 쿨다운",
                key,
                health.consecutive_failures,
                health.cooldown_until.strftime("%H:%M:%S"),
            )

    def record_rate_limited(self, key: str, error: str) -> None:
        """Immediately cool down a source that explicitly returned HTTP 429."""
        health = self._health.get(key)
        if health is None:
            return
        health.consecutive_failures = self._failure_threshold
        health.last_error = error[:200]
        health.cooldown_until = now() + self._cooldown
        logger.warning(
            "[NEWS] 소스 %s 요청 제한(429): %s까지 쿨다운",
            key,
            health.cooldown_until.strftime("%H:%M:%S"),
        )

    def record_unavailable(self, key: str, error: str) -> None:
        """Cool down a public source that is unreachable or does not respond."""
        health = self._health.get(key)
        if health is None:
            return
        health.consecutive_failures = self._failure_threshold
        health.last_error = error[:200]
        health.cooldown_until = now() + self._cooldown
        logger.warning(
            "[NEWS] source %s unavailable; cooldown until %s",
            key,
            health.cooldown_until.strftime("%H:%M:%S"),
        )

    def status_lines(self) -> list[str]:
        """/system 표시용 소스 상태 요약."""
        current = now()
        lines = []
        for spec in self._specs:
            health = self._health[spec.key]
            if health.cooldown_until and current < health.cooldown_until:
                state = f"쿨다운(~{health.cooldown_until.strftime('%H:%M')})"
            elif health.consecutive_failures > 0:
                state = f"실패 {health.consecutive_failures}회"
            else:
                state = "정상"
            lines.append(f"{spec.key}: {state}")
        return lines
