"""News pipeline data contracts."""

from __future__ import annotations

from dataclasses import dataclass

from shared.llm.translator import TranslationResult
from telegram_bot.news.registry import SourceSpec
from telegram_bot.news.sources import GlobalArticle

@dataclass(frozen=True)
class SourceCandidate:
    """번역 전 후보 한 건. 주간 번역과 야간 큐가 같은 목록을 본다."""

    spec: SourceSpec
    article: GlobalArticle
    prefilter_candidate_id: str = ""
    event_id: str = ""


@dataclass(frozen=True)
class PreparedGlobalArticle:
    spec: SourceSpec
    article: GlobalArticle
    text: str
    translated: TranslationResult
    prefilter_candidate_id: str = ""


@dataclass(frozen=True)
class PreparedSourceSection:
    rows: list[PreparedGlobalArticle]
    text: str


# ── 뉴스 수집 함수 ────────────────────────────────────


