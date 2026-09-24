"""조언 한 건을 만든다: 외부 자료 → 규칙 진단 → LLM 해석 → 저장.

한 사람이 쓰는 화면이라 작업 큐가 없다. `POST /api/portfolio/advice`가 끝까지
기다려 완성된 조언을 돌려준다(수십 초). 비용은 둘로 막는다 — 동시 실행 잠금
하나(겹치면 409)와 한국 시간 하루 상한(`PORTFOLIO_ADVICE_MAX_DAILY`, 넘으면 429).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from services.web.core.clock import now
from services.web.portfolio import market_data
from services.web.portfolio.advisor import AdviceError, PortfolioAdvisor
from services.web.portfolio.diagnosis import diagnose
from services.web.portfolio.store import AdviceStore, AssetStore

logger = logging.getLogger(__name__)

# 한 번에 실거래가를 추정하는 부동산 수. 한 건에 API가 달 수만큼 나간다.
_MAX_ESTIMATES = 5


class AdviceBusy(RuntimeError):
    """이미 조언을 만드는 중이다."""


class AdviceLimit(RuntimeError):
    """오늘 상한에 닿았다."""


def _market_context(public_dir: Path) -> dict[str, Any]:
    """공개 산출물의 시장별 뉴스 논조. 가격이 아니라 보도 논조의 평균이다."""
    try:
        payload = json.loads((public_dir / "market.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sentiment = {
        market: round(float(row["avg_sentiment"]), 2)
        for market, row in (payload.get("markets") or {}).items()
        if isinstance(row, dict) and row.get("avg_sentiment") is not None
    }
    return {"news_sentiment": sentiment, "news_as_of": payload.get("generated_at")}


class AdviceService:
    def __init__(
        self,
        *,
        assets: AssetStore,
        advice: AdviceStore,
        public_dir: Path,
        max_daily: int,
        history_limit: int,
        advisor_factory: Callable[[], PortfolioAdvisor],
        fetch_deposit_rates: Callable[[], dict[str, Any]] = market_data.fetch_deposit_rates,
        fetch_market_rates: Callable[[], dict[str, Any]] = market_data.fetch_market_rates,
        estimate_property: Callable[..., dict[str, Any]] = market_data.estimate_property,
    ):
        self._assets = assets
        self._advice = advice
        self._public_dir = public_dir
        self._max_daily = max_daily
        self._history_limit = history_limit
        self._advisor_factory = advisor_factory
        self._fetch_deposit_rates = fetch_deposit_rates
        self._fetch_market_rates = fetch_market_rates
        self._estimate_property = estimate_property
        self._running = threading.Lock()

    def usage(self) -> dict[str, int]:
        return {"today": self._advice.count_on(now().strftime("%Y%m%d")), "max_daily": self._max_daily}

    def create(self) -> dict[str, Any]:
        if not self._running.acquire(blocking=False):
            raise AdviceBusy("이미 조언을 만드는 중입니다.")
        try:
            if self.usage()["today"] >= self._max_daily:
                raise AdviceLimit(f"오늘은 {self._max_daily}회까지 만들 수 있습니다.")
            return self._create()
        finally:
            self._running.release()

    def _create(self) -> dict[str, Any]:
        moment = now()
        assets = self._assets.list()
        deposit_rates = self._fetch_deposit_rates()
        market_rates = self._fetch_market_rates()
        estimates: dict[str, dict[str, Any]] = {}
        for asset in [a for a in assets if a.get("kind") == "real_estate"][:_MAX_ESTIMATES]:
            if asset.get("region_code") and asset.get("area_m2"):
                estimates[str(asset["id"])] = self._estimate_property(
                    str(asset["region_code"]), float(asset["area_m2"]),
                    complex_name=str(asset.get("complex") or ""), today=moment.date(),
                )
        report = diagnose(assets, today=moment.date(), deposit_rates=deposit_rates,
                          market_rates=market_rates, estimates=estimates)
        sources = {"deposit_rates": deposit_rates.get("status"), "market_rates": market_rates.get("status")}
        if estimates:
            states = {row.get("status") for row in estimates.values()}
            sources["real_estate"] = "ok" if states == {"ok"} else sorted(s for s in states if s != "ok")[0]
        context = _market_context(self._public_dir)
        context["missing"] = sorted(name for name, state in sources.items() if state != "ok")

        text, llm_status = None, "skipped_empty"
        if assets:
            try:
                text = self._advisor_factory().advise(report, context)
                llm_status = "ok"
            except (AdviceError, RuntimeError) as error:
                # 자격증명 없음(ConfigurationError)도 여기로 온다. 진단은 그대로 쓸모가 있다.
                logger.warning("[PORTFOLIO] 조언 본문 실패, 진단만 저장: %s", error)
                llm_status = f"failed: {str(error)[:120]}"

        advice = {
            "id": self._advice.new_id(),
            "created_at": moment.isoformat(timespec="seconds"),
            "text": text,
            "llm_status": llm_status,
            "sources": sources,
            "diagnosis": report,
            "market_context": context,
            "disclaimer": "참고 정보이며 투자 권유가 아닙니다. 결정과 책임은 본인에게 있습니다.",
        }
        self._advice.save(advice)
        self._advice.prune(self._history_limit)
        return advice
