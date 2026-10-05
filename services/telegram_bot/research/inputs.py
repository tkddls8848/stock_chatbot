"""웹 개인 리서치의 입력 묶음을 굽는다(`storage/public/research_inputs.json`).

웹 계정의 리서치는 운영자 리서치(`research/job.py`)와 같은 프롬프트·분석으로 돈다. 웹은 봇 코드를
import하지 않고 외부 시세를 부르지 않으므로, 봇이 같은 수집기(`research/news.py`)·후보 구성
(`job._build_candidates`)·섹터 요약을 **관심종목 없이** 돌려 파일로 남기고 웹은 그것을 읽는다.
계정의 관심종목은 웹이 실행 때 후보 앞에 붙인다. 운영자 주제·관심종목은 이 파일에 들어가지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram.ext import Application

from services.telegram_bot.core.workers import run_non_urgent
from services.telegram_bot.research.job import _build_candidates

logger = logging.getLogger(__name__)


async def refresh_research_inputs(app: Application) -> dict[str, Any] | None:
    """입력 묶음을 한 번 굽는다. 뉴스가 없으면 직전 파일을 그대로 두고 None."""
    bot_data = app.bot_data
    news_items = await bot_data["research_news_collector"]()
    if not news_items:
        logger.warning("[RESEARCH] 웹 리서치 입력: 최근 시장 뉴스가 없어 굽지 않았다.")
        return None
    candidates = await _build_candidates(bot_data, {}, news_items)

    sector_summary_context = None
    quote_service = bot_data.get("quote_service")
    if quote_service is not None:
        try:
            sector_summary_context = await run_non_urgent(
                quote_service.build_sector_summary_context, {}, False
            )
        except Exception as error:
            logger.warning("[RESEARCH] 웹 리서치 입력: 요약 컨텍스트 수집 실패: %s", error)

    from services.telegram_bot.publish import publish_research_inputs

    await run_non_urgent(publish_research_inputs, news_items, candidates, sector_summary_context)
    logger.info("[RESEARCH] 웹 리서치 입력을 구웠다: 뉴스 %d건, 후보 %d개", len(news_items), len(candidates))
    return {"news_count": len(news_items), "candidate_count": len(candidates)}
