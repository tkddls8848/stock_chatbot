"""`news/utils.py`의 표시·시각·자르기 계약.

예약 뉴스가 화면에 내보내는 모든 문자열이 이 함수들을 지난다
(`news/report.py`의 3시간 시장상황 보고서). 자르기·이스케이프 순서나 시각
표기가 바뀌면 텔레그램 메시지가 깨지거나 링크가 사라지므로 여기서 고정한다.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services.telegram_bot.core.config import (
    NEWS_DIGEST_ARTICLE_MAX_CHARS,
    NEWS_DIGEST_TITLE_MAX_CHARS,
)
from services.telegram_bot.news.sources import GlobalArticle
from services.telegram_bot.news.utils import (
    chunk_message_items,
    compact_jst_time,
    compact_sentiment_line,
    filter_articles_for_jst_day,
    filter_recent_articles,
    format_china_time_as_jst,
    format_digest_article,
    truncate_at_sentence,
    truncate_text,
)

def test_truncate_text_remains_available_for_titles():
    result = truncate_text("가" * 150, 100)

    assert len(result) == 100
    assert result.endswith("...")

def test_truncate_text_keeps_short_text():
    assert truncate_text("짧은 기사 요약", 100) == "짧은 기사 요약"

def test_chunk_message_items_keeps_articles_whole_and_ordered():
    items = ["가" * 40, "나" * 40, "다" * 40]

    chunks = chunk_message_items(
        items,
        text_getter=lambda item: item,
        max_body_length=81,
        separator="\n",
    )

    assert chunks == [[items[0], items[1]], [items[2]]]
    assert [item for chunk in chunks for item in chunk] == items

def test_china_article_timestamp_includes_date_and_time_in_jst():
    assert (
        format_china_time_as_jst("23:30:00", "2026-07-18")
        == "2026-07-19 00:30:00 JST"
    )

def test_rss_article_timestamp_respects_source_timezone():
    assert (
        format_china_time_as_jst("Fri, 17 Jul 2026 14:30:00 GMT")
        == "2026-07-17 23:30:00 JST"
    )

def test_recent_article_filter_drops_stale_unknown_and_future_items():
    now = datetime(2026, 7, 19, 12, tzinfo=timezone(timedelta(hours=9)))
    recent = GlobalArticle("recent", "recent", "", "Sun, 19 Jul 2026 01:00:00 GMT")
    stale = GlobalArticle("stale", "stale", "", "Fri, 19 Jun 2026 01:00:00 GMT")
    unknown = GlobalArticle("unknown", "unknown", "", "")
    future = GlobalArticle("future", "future", "", "Mon, 20 Jul 2026 12:00:00 GMT")

    assert filter_recent_articles(
        [stale, unknown, recent, future],
        max_age_hours=48,
        now=now,
    ) == [recent]

def test_calendar_day_filter_uses_jst_date_and_sorts_newest_first():
    early = GlobalArticle("early", "early", "", "Fri, 03 Jul 2026 15:30:00 GMT")
    late = GlobalArticle("late", "late", "", "Sat, 04 Jul 2026 10:00:00 GMT")
    previous = GlobalArticle("previous", "previous", "", "Fri, 03 Jul 2026 14:59:00 GMT")

    assert filter_articles_for_jst_day(
        [early, previous, late],
        datetime(2026, 7, 4).date(),
    ) == [late, early]

def test_article_display_keeps_only_jst_time():
    assert compact_jst_time("2026-07-19 09:15:00 JST") == "09:15:00 JST"
    assert compact_jst_time("2026-07-19 09:15 JST") == "09:15 JST"

def test_article_display_migrates_legacy_kst_label_without_shifting_time():
    assert compact_jst_time("2026-07-19 09:15:00 KST") == "09:15:00 JST"

def test_digest_article_uses_text_file_layout():
    assert format_digest_article(
        "기사 제목",
        "기사 본문",
        "09:15:00 JST",
        "- 감성 : 긍정 +0.50 · 영향 높음",
    ) == (
        "• 기사 제목 (09:15:00 JST)\n"
        "- 기사 본문\n"
        "- 감성 : 긍정 +0.50 · 영향 높음"
    )

def test_digest_article_caps_body_at_the_display_limit():
    body = format_digest_article("제목", "본" * 400, "09:15:00 JST").split("\n")[1]

    assert len(body) == len("- ") + NEWS_DIGEST_ARTICLE_MAX_CHARS
    assert body.endswith("...")

def test_digest_article_caps_title_at_the_display_limit():
    title_line = format_digest_article("제" * 150, "본문", "09:15:00 JST").split("\n")[0]

    assert title_line == (
        "• " + "제" * (NEWS_DIGEST_TITLE_MAX_CHARS - 3) + "... (09:15:00 JST)"
    )

def test_digest_article_truncates_before_escaping():
    """자른 뒤에 escape한다 — HTML 엔티티가 중간에서 잘리지 않는다.

    순서를 뒤집으면 `&amp;`가 `&am`으로 끊겨 텔레그램이 메시지 전체를
    파싱 오류로 거부한다.
    """
    body = format_digest_article("제목", "&" * 400, "09:15:00 JST").split("\n")[1]

    assert body.count("&amp;") == NEWS_DIGEST_ARTICLE_MAX_CHARS - 3
    assert body.endswith("...")
    assert "&am" not in body.replace("&amp;", "")

def test_digest_article_keeps_original_link_on_title():
    assert format_digest_article(
        "기사 제목",
        "기사 본문",
        "2026-07-19 09:15 JST",
        url="https://example.com/news?a=1&b=2",
    ).startswith(
        '• <a href="https://example.com/news?a=1&amp;b=2">기사 제목</a> '
    )

def test_body_is_cut_at_a_sentence_boundary_not_mid_word():
    """상한에 걸린 본문을 문장 한가운데에서 끊으면 반 문장만 남는다."""
    first = "가" * 140 + "다."
    body = first + " " + "두 번째 문장이 길게 이어진다" * 20

    result = truncate_at_sentence(body, NEWS_DIGEST_ARTICLE_MAX_CHARS)

    assert result == first

def test_body_falls_back_to_character_cut_when_the_sentence_ends_too_early():
    """경계가 너무 앞이면 버리는 내용이 더 많다. 그때는 글자로 자른다."""
    body = "짧다. " + "이어지는 긴 문장이 계속된다" * 30

    result = truncate_at_sentence(body, NEWS_DIGEST_ARTICLE_MAX_CHARS)

    assert result.endswith("...")
    assert len(result) == NEWS_DIGEST_ARTICLE_MAX_CHARS

def test_body_within_the_limit_is_untouched():
    assert truncate_at_sentence("한 문장이다. 두 문장이다.", 100) == "한 문장이다. 두 문장이다."

def test_digest_article_without_a_body_keeps_only_the_title_line():
    """야간 다이제스트는 제목만 옮긴다. 빈 본문 줄을 남기면 '- '만 보인다."""
    text = format_digest_article("제목", "", "09:15 JST", compact_sentiment_line(0.4, "high"))

    assert text.splitlines() == [
        "• 제목 (09:15 JST)",
        "- 감성 : 긍정 +0.40 · 영향 높음",
    ]


# ── 분석 재료 거르기(속보·제목 없음)·매체명 꼬리 (운영자 결정 2026-10-02) ──────────────

@pytest.mark.parametrize("title", [
    "[속보] 코스피 7000 돌파", "  [속보]코스피", "【速報】日経平均", "【快讯】上证指数", "（快讯）沪指",
    "快讯：沪指收涨", "BREAKING: Fed cuts", "breaking news | Fed", "[Breaking] Fed cuts",
    "＜속보＞ 환율 급등", "(긴급) 정부 발표",
])
def test_flash_titles_are_recognised_by_their_leading_marker(title):
    from services.telegram_bot.news.utils import is_flash_title
    assert is_flash_title(title)


@pytest.mark.parametrize("title", [
    "속보 이후 반등한 코스피", "Fed's breaking point on rates", "A股快讯类资讯减少的原因", "", "코스피 [속보] 이후",
])
def test_words_inside_a_title_are_not_a_flash_marker(title):
    from services.telegram_bot.news.utils import is_flash_title
    assert not is_flash_title(title)


def test_analyzable_articles_drop_flash_and_untitled():
    from services.telegram_bot.news.utils import analyzable_articles
    articles = [GlobalArticle("a", "", "본문만", ""), GlobalArticle("b", "【速報】株価", "", ""),
                GlobalArticle("c", "반도체 업황 회복과 코스피 전망", "", ""),
                GlobalArticle("d", "(株)モダリス【4883】：株価・株式情報（夜間PTS含む）", "", ""),
                GlobalArticle("e", "Pfizer Inc. (PFE) Stock Price, News, Quote & History", "", ""),
                GlobalArticle("f", "日経平均株価、終値647円安 半導体株に売り", "", ""),
                GlobalArticle("g", "必威买球泽连斯基在纽约与特朗普会晤后表示", "", ""),
                GlobalArticle("h", "澳门博彩股走强 国庆黄金周客流回升", "", ""),
                GlobalArticle("i", "ManBetx手机版武契奇辞去塞尔维亚总统职务", "", ""),
                GlobalArticle("j", "yabovip188登录武契奇辞去塞尔维亚总统职务", "", ""),
                GlobalArticle("k", "武契奇辞去塞尔维亚总统职务", "", "", extra={"publisher": "Pchome电脑之家"}),
                GlobalArticle("l", "Bet365 plans London listing as profits rise", "", ""),
                GlobalArticle("m", "Yabotech raises funding for battery recycling", "", ""),
                GlobalArticle("n", "ManBetx reports annual earnings", "", ""),
                GlobalArticle("o", "监管部门点名必威等境外博彩网站", "", "")]
    # 시세 화면(d·e)·도박 광고(g·i·j)·광고를 끼워 넣는 매체(k)는 거른다. 주가 기사(f)·카지노주 기사(h)·
    # 상표로 시작해도 광고 꼴이 아닌 기업 기사(l·m·n)·제목 중간의 상표명(o)은 남긴다.
    assert [a.article_id for a in analyzable_articles(articles)] == ["c", "f", "h", "l", "m", "n", "o"]


# 2026-10-02~06 서버 KR 큐에서 그대로 옮긴 제목들이다.
@pytest.mark.parametrize("title", [
    "규칙 문구의 이상과 초과, 로얄 슬롯 조건 해석",
    "게임 설명 속 변동성이라는 말, 구조대 토토 먹튀 용어 풀이",
    "알림창을 닫기 전 읽어야 할 바카라 깡 디시 내용",
    "게임별 화면 구성을 비교하는 강원랜드 카지노 관찰 포인트",
    "현재 단계가 헷갈릴 때 보는 홀덤 올인 폴드 상태 안내",
    "카지노 777 : 학습 전략과 자원 - 최신 트렌드",
    "불법영화사이트 신고 의 비밀을 풀다: 전문가들이 공유하는 핵심 팁",
    "브로드컴 (AVGO) 주식 움직였습니다 상승 3.26%에 10월2일: 변동 원인",
    "Bitcoin(BTCUSD) 종목이 10월4일에 갑자기 1.01% 상승한 상황에서 무엇을 주목해야 할까요?",
    "[MK시그널] 오오마 매도신호 포착, 수익률 78.5% 달성",
    "▶▶이번주 마이크론 테크놀로지의 실적 공개, 투자자들이 먼저 찾는 종목 ▶▶【AI골든봇】 추천종목 공개",
    "+225% 수익률과 계속되는 상승세: AI가 고른 이 기술주들이 시장을 압도하고 있습니다",
    "[과매도 우량주 리포트] 🚨RSI 과매도 구간 진입한 우량주, 섹터별로 짚어봅니다",
    "[게시판] 키움증권, 연금저축·중개형ISA 개설 이벤트",
    "“ETF 사고 최대 27만원 받는다”…키움증권, ISA 이벤트 진행",
    "iM뱅크, 창립 59주년 기념 '매주 200만원 현금 경품' 이벤트",
])
def test_korean_advertisements_are_not_analyzed(title):
    from services.telegram_bot.news.utils import analyzable_articles
    assert analyzable_articles([GlobalArticle("a", title, "", "")]) == []


# 광고 낱말과 겹치지만 시장 기사다. 카지노주 두 줄과 금감원 줄은 같은 꼴로 지은 예이고, 나머지는
# 같은 기간 같은 큐에 실제로 들어왔다.
@pytest.mark.parametrize("title", [
    "파라다이스, 회사채 수요예측에 3.4배 몰려…2·3년물 온도차(종합)",
    "강원랜드, 카지노 매출 회복에 실적 개선 기대",
    "카지노주 강세…중국 단체관광 재개 기대",
    "외국인, SK스퀘어·SK이노·대한항공에 1.3조 베팅",
    "[증시 레이더] 코스피, 美 국채금리 급등에 이틀째 하락⋯6,870선 마감",
    "코스닥과 다른길! 게임주 약세…빅3 '동반 하락'",
    "컴투스, 신작게임 '제우스' 흥행에 호실적 기대…목표가↑",
    "코스피 0.48% 하락‥마이크론 실적 등 대형 이벤트 앞두고 경계감 증시에 부담",
    "“프로야구팬에 치킨 준다”는 증권사 마케팅, 금감원이 과열 경고",
    "골드만삭스가 실적 발표 앞두고 추천한 반도체 주식 3종목",
])
def test_market_articles_sharing_ad_words_are_kept(title):
    from services.telegram_bot.news.utils import analyzable_articles
    assert len(analyzable_articles([GlobalArticle("a", title, "", "")])) == 1


def test_korean_gambling_seo_publisher_is_dropped_whatever_the_title():
    from services.telegram_bot.news.utils import analyzable_articles
    article = GlobalArticle("a", "기본 조작은 몇 단계로 이뤄질까? 카지노 여자배우", "", "",
                            extra={"publisher": "Calgary Roughnecks"})
    assert analyzable_articles([article]) == []


@pytest.mark.parametrize(("title", "expected"), [
    ("코스피 7000선 회복 - 연합뉴스", "코스피 7000선 회복"),
    ("Fed holds rates - The Economic Times", "Fed holds rates"),
    ("株価 - Yahoo!ファイナンス", "株価 - Yahoo!ファイナンス"),   # 남는 제목이 너무 짧으면 그대로
    ("제목에 하이픈이 없다", "제목에 하이픈이 없다"),
    ("코스피 7000 탈환 - 머니투데이 - 머니투데이", "코스피 7000 탈환"),   # 반복 꼬리
    # 매체명을 모르므로 제목 자체의 마지막 하이픈 구절도 뗀다 — 사전선별 벡터 전용이라 감수한다.
    ("금리 정책 발표 - 시행 전 점검", "금리 정책 발표"),
])
def test_publisher_tail_heuristic_for_titles_without_a_known_publisher(title, expected):
    from services.telegram_bot.news.utils import strip_publisher_tail
    assert strip_publisher_tail(title) == expected
