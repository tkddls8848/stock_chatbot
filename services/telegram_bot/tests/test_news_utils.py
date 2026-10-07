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


# 2026-09-30~10-07 사전선별 관측(현재 규칙을 통과한 7,086건)에서 그대로 옮긴 제목들이다.
@pytest.mark.parametrize(("title", "reason"), [
    ("Omnicom Group Inc. (OMC) stock price, news, quote and history", "시세·목록 화면"),
    ("S&P 500 (^GSPC) Charts, Data & News", "시세·목록 화면"),
    ("日経連続増配株指数 チャート・推移・終値:株価指数", "시세·목록 화면"),
    ("本日のランキング【値上がり率】 (10月7日)", "시세·목록 화면"),
    ("◎午前１０時現在の値上がり値下がり銘柄数", "시세·목록 화면"),
    ("[PTS]ナイトタイムセッション17時30分時点 上昇137銘柄・下落134銘柄（東証終値比）", "시세·목록 화면"),
    ("【注目トピックス 日本株】前日に動いた銘柄 part1メイコー、日東紡績、マネーフォワードなど", "시세·목록 화면"),
    ("[오프로]26.10.06 상한가 및 상승종목", "시세·목록 화면"),
    ("[연합뉴스 이 시각 헤드라인] - 07:30", "시세·목록 화면"),
    ("외국환시세(10월7일·15:30 기준가)", "시세·목록 화면"),
    ("META_TITLE_SECTORS", "시세·목록 화면"),
    ("News & Analysis", "시세·목록 화면"),
    ("EUR/USD (EURUSD) Is down 0.58% on Oct 7: Why It Happened", "자동 생성 글"),
    ("Applied Materials stock gained 2.03 percent on October 2 in Nasdaq trading", "자동 생성 글"),
    ("日本製鉄(株)【5401】：今の株価の理由は？値動きの背景をAIが解説", "자동 생성 글"),
    ("AI주식상승확률분석 : LG전자 (066570) 주가 전망과 상승확률 분석", "자동 생성 글"),
    ("[온체인 주식선물] 현대차, 정규장 종가보다 0.97% 웃돈", "자동 생성 글"),
    ("[MK 골든크로스 돌파종목 : 코오롱생명과학(102940) & 이미지스(115610)]", "광고·홍보"),
    ("브라질 증시 선거 후 8% 급등: AI 추천 유통주 50% 상승", "광고·홍보"),
    ("【大化け】6月分割注目の超優良銘柄！ Josh Hart (iWWAt1RFVW)", "광고·홍보"),
    ("3 European Defense Stocks Investors Are Watching As NATO Risk Returns", "종목 추천 글"),
    ("5 Best Energy Stocks for 2026 and How to Invest", "종목 추천 글"),
    ("FAE Technology And 2 Other European Penny Stocks With Promising Fundamentals", "종목 추천 글"),
    ("AI Agents Are Creating a New Cybersecurity Boom: 3 Stocks to Watch", "종목 추천 글"),
    ("Is It Too Late to Buy Micron Technology Stock After Its 12-Month Gain of 500%?", "종목 추천 글"),
    ("Tesla Stock Forecast | Record Deliveries, Robotaxi Expansion", "종목 추천 글"),
    ("円高メリット銘柄8選｜ニトリ・食品・航空の注目株を比較！", "종목 추천 글"),
    ("元鼎证券观察：四季度行情明日启幕，A股从“假期交易”转向“产业筛选”", "종목 추천 글"),
    ("[서치 e종목] 가온전선, LSCUS 5.2조 버스덕트 수주 모멘텀으로 주가 우상향?", "종목 추천 글"),
    ("[인사] 한화투자증권", "시장 무관 난"),
    ("[부고] 조성길(스트리미 이사)씨 모친상", "시장 무관 난"),
    ("[동포의 창] 세계한상대회 폐막…3억달러 상담·940만달러 현장 계약 성과", "시장 무관 난"),
    ("【2014（平成26）年10月7日】LEDで日本人3人にノーベル賞", "시장 무관 난"),
])
def test_titles_that_are_not_articles_are_not_analyzed(title, reason):
    from services.telegram_bot.news.utils import analyzable_articles, exclusion_reason
    assert exclusion_reason(title) == reason
    assert analyzable_articles([GlobalArticle("a", title, "", "")]) == []


# 위 규칙과 낱말이 겹치지만 기사다. 마지막 셋은 같은 꼴로 지은 예이고 나머지는 같은 관측에 있었다.
@pytest.mark.parametrize("title", [
    "[클릭 e종목]롯데칠성, 3분기 실적 기대치 하회 전망…목표가↓",
    "[오늘의 주목주] 삼성전자우 주가 4%대 내려, 코스피 외국인·기관 매도세에 6830선 약보합 마감",
    "[외환] 원/달러 환율 3.2원 내린 1,340.4원(15:30 기준가)",
    "한은 국제 담당 부총재보에 최영주…경제연구원장에 윤경수",
    "Jefferies Names Top Japan Semiconductor Equipment Stocks to Buy",
    "Nvidia soars near US$6 tril market cap with stock back at high",
    "日経平均終値648円安、インフレ懸念で息切れ 米株高に追随できず",
    "Goldman raises oil price forecast to $100 as Hormuz standoff drags on",
    "3 stocks dragged the Nikkei lower as chipmakers slid",
    "[종목 포커스] 한스바이오메드, 휴젤 판매 개시로 흑자 전환",
])
def test_articles_sharing_words_with_the_non_article_rules_are_kept(title):
    from services.telegram_bot.news.utils import exclusion_reason
    assert exclusion_reason(title) == ""


def test_partition_counts_what_was_dropped_and_why():
    from services.telegram_bot.news.utils import partition_analyzable
    articles = [GlobalArticle("a", "", "", ""), GlobalArticle("b", "[속보] 환율 급등", "", ""),
                GlobalArticle("c", "3 UK Penny Stocks With Market Caps Up To £400M", "", ""),
                GlobalArticle("d", "3 European Growth Stocks With Up To 33% Insider Ownership", "", ""),
                GlobalArticle("e", "한은, 기준금리 동결", "", ""),
                GlobalArticle("f", "南方财经全媒体集团", "", "", extra={"publisher": "南方财经全媒体集团"})]
    kept, dropped = partition_analyzable(articles)
    assert [article.article_id for article in kept] == ["e"]
    assert dropped == {"제목 없음": 1, "속보": 1, "종목 추천 글": 2, "시세·목록 화면": 1}


def test_junk_reason_leaves_flash_and_untitled_to_the_input_filter():
    """`junk_reason`은 이미 공개한 근거를 지울 때도 쓴다. 속보는 기사이고, 원문 제목을 남기지 않던 근거는 판정할 수 없다."""
    from services.telegram_bot.news.utils import junk_reason
    assert junk_reason("[속보] 코스피 7000 돌파") == ""
    assert junk_reason("", "") == ""
    assert junk_reason("3 Defense Stocks Worth Watching As Europe Security Spending Rises") == "종목 추천 글"


# ── 장 시황 판정 (사전선별이 구간마다 한 건으로 묶는다) ───────────────────
@pytest.mark.parametrize(("market", "title", "expected"), [
    ("KR", "코스피, 0.89% 하락한 6941.39 마감…코스닥 2.98%↑(2보)", "KR"),
    ("KR", "[개장시황] 코스피, 뉴욕증시 강세에도 장 초반 6900선 등락", "KR"),
    ("KR", "뉴욕증시 최고치에도 코스피 1.28% 하락", "KR"),   # 수집한 시장의 지수가 먼저다
    ("KR", "미 국채금리·유가 하락 뉴욕증시 강세…S&P500·나스닥 사상 최고", "US"),
    ("KR", "6일 VN지수 +0.34% “1750선 회복했지만, 저항 압력 여전”", "VN"),
    ("US", "S&P 500 closes at record high as Nvidia nears $6 trillion milestone", "US"),
    ("US", "Wall Street futures rise after softer-than-expected U.S. inflation data", "US"),
    ("JP", "東証終値648円安", "JP"),
    ("JP", "（朝）米国市場は主要3指数揃って上昇　大型ハイテク株が相場を牽引し買い優勢の展開", "US"),
    ("EU", "European shares gain as Genmab jumps, bond yields retreat", "EU"),
    ("HK", "恒指半日跌128點　科指跌近1%", "HK"),
    ("HK", "日股半日跌近1%", "JP"),
    ("CN", "美股收盘：三大股指收涨 埃森哲涨近16%", "US"),
])
def test_index_recaps_are_recognised_with_their_index(market, title, expected):
    from services.telegram_bot.news.utils import market_recap_index
    assert market_recap_index(title, market) == expected


# 지수 이름과 등락 표지가 함께 있지만 장 시황이 아니다. 잘못 묶이면 같은 구간의 장 시황에 밀려 빠진다.
@pytest.mark.parametrize(("market", "title"), [
    ("KR", "[단독]작년 코스닥특례상장 95% 같은해 실적마저 부풀렸다[코스닥 뻥튀기 상장]①"),
    ("KR", "美 증시는 사상 최고치인데 코스피는 왜 7000에서 번번이 미끄러지나"),
    ("KR", "900선 뚫은 코스닥…이번 반등은 진짜일까 [박진우의 개미수다]"),
    ("KR", "대만, 韓 제치고 올해 증시 수익률 1위…가권 72% vs 코스피 65%"),
    ("KR", "외국인, 9월 한달 간 코스피서 20조 '셀 코리아'... '삼전닉스' 비중 40% 이상"),
    ("KR", "다우기술, 3분기 영업이익 12% 증가"),
    ("US", "Wall Street bonuses poised to hit record as annual profits forecast to top US$90 billion"),
    ("US", "Dow Inc shares fall 5% after weak guidance"),
    ("JP", "上限25億円の「自社株買い」発表で大幅反発…〈東証プライム・値上がり2位〉となった注目銘柄"),
    ("CN", "耐克股价美股盘后跌幅扩大至8.4%"),
    ("EU", "NOK Stock Gains After-Hours — Nokia Oyj Rejoins Europe's Benchmark Stoxx 50 Index"),
    ("KR", "삼성전자, 3분기 영업이익 100조 돌파 전망에 외국인 순매수"),
])
def test_titles_that_only_mention_an_index_are_not_recaps(market, title):
    from services.telegram_bot.news.utils import market_recap_index
    assert market_recap_index(title, market) == ""


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
