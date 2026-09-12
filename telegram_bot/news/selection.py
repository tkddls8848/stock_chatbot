"""Choose which prepared articles are sent in a digest."""

from telegram_bot.news.models import PreparedGlobalArticle

_IMPACT_RANK = {"high": 0, "medium": 1, "low": 2}


def _digest_priority(index: int, row: PreparedGlobalArticle) -> tuple[int, int, float, int]:
    """송출 우선순위 정렬 키(작을수록 먼저 보낸다).

    impact가 1순위, 같으면 감성의 세기, 그래도 같으면 최신순이다. impact나
    sentiment가 없는 건은 같은 순위 안에서 제일 뒤로 민다.
    """
    translated = row.translated
    sentiment = translated.sentiment
    return (
        _IMPACT_RANK.get(translated.impact, len(_IMPACT_RANK)),
        0 if sentiment is not None else 1,
        -abs(sentiment) if sentiment is not None else 0.0,
        # prepared_rows는 과거→최신 순이라 index가 클수록 새 기사다.
        -index,
    )


def select_digest_rows(
    rows: list[PreparedGlobalArticle],
    send_limit: int,
) -> tuple[list[PreparedGlobalArticle], list[PreparedGlobalArticle]]:
    """번역된 소스 하나의 기사를 (송출 대상, 탈락)으로 나눈다.

    골라낸 쪽은 원래의 과거→최신 표시 순서를 유지한다. 탈락한 쪽도 번역과
    감성 분석은 이미 끝났으므로 버리지 않는다 — 확정과 로그 기록은
    `archive_unsent_articles`가 맡는다.
    """
    if len(rows) <= send_limit:
        return list(rows), []
    ranked = sorted(
        range(len(rows)),
        key=lambda index: _digest_priority(index, rows[index]),
    )
    selected = set(ranked[:send_limit])
    return (
        [row for index, row in enumerate(rows) if index in selected],
        [row for index, row in enumerate(rows) if index not in selected],
    )



