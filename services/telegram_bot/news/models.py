"""수집 경로가 주고받는 데이터 계약."""

from __future__ import annotations

from dataclasses import dataclass

from services.telegram_bot.news.registry import SourceSpec
from services.telegram_bot.news.sources import GlobalArticle


@dataclass(frozen=True)
class SourceCandidate:
    """보고서용 원문 후보 한 건.

    `collect_source_candidates`가 만들고 `collect_report_source`가 큐에 담는다.
    `prefilter_candidate_id`는 사전선별이 매긴 후보 식별자로, 나중에 라벨을
    이어 붙일 수 있게 큐 항목까지 따라간다.
    """

    spec: SourceSpec
    article: GlobalArticle
    prefilter_candidate_id: str = ""
    event_id: str = ""
    prefilter_exploration: bool = False
