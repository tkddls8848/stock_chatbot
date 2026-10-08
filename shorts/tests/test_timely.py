from datetime import datetime

from polymarket_shorts.timely import related_news

REFERENCE = datetime.fromisoformat("2026-10-08T20:00:00+09:00")


def article(title, stamp="2026-10-08T10:00:00+09:00", text=""):
    return {"kind": "news", "title": title, "text": text or title, "published_at": stamp,
            "market": "US", "source": "Wire", "url": ""}


def issue(*keywords, title="Crude Oil all time high by...?"):
    return {"title": title, "markets": [], "selection": {"keywords": list(keywords)}}


def test_only_articles_naming_the_subject_are_candidates():
    # 낱말 겹침("사상 최고치")만으로는 원유 질문에 주가 신기록 기사가 붙었다(2026-10-08 실측).
    pool = [article("미국 주식시장, AI 투자심리로 사상 최고치 기록"), article("후티 위협으로 유가 상승")]
    assert [row["title"] for row in related_news(issue("유가", "원유"), pool, REFERENCE)] == ["후티 위협으로 유가 상승"]


def test_without_subject_keywords_nothing_is_attached():
    assert related_news(issue(), [article("후티 위협으로 유가 상승")], REFERENCE) == []


def test_subject_in_the_lead_clause_outranks_an_index_recap_listing_it():
    recap = article("S&P 500, 나스닥 신고점... 국제유가 하락", stamp="2026-10-08T19:00:00+09:00")
    lead = article("유가 급등에 에너지주 강세", stamp="2026-10-07T09:00:00+09:00")
    rows = related_news(issue("유가"), [recap, lead], REFERENCE)
    assert [row["title"] for row in rows] == [lead["title"], recap["title"]]
    assert [row["when"] for row in rows] == ["어제", "오늘"]
    assert [row["id"] for row in rows] == ["market:1", "market:2"]


def test_original_text_counts_when_the_translated_title_misses_the_subject():
    row = article("에너지 가격 상승", text="Oil prices climb after OPEC cut")
    assert related_news(issue("OPEC"), [row], REFERENCE)[0]["original"] == "Oil prices climb after OPEC cut"


def test_english_keywords_match_whole_words_only():
    assert related_news(issue("Fed"), [article("Federation cup", text="Federation cup")], REFERENCE) == []


def test_duplicates_future_and_stale_articles_are_dropped():
    pool = [article("유가 상승"), article("유가  상승"),
            article("유가 폭등 예고", stamp="2026-10-08T21:00:00+09:00"),
            article("유가 하락", stamp="2026-10-05T10:00:00+09:00"),
            article("유가 소식", stamp="")]
    assert [row["title"] for row in related_news(issue("유가"), pool, REFERENCE)] == ["유가 상승"]


def test_day_label_uses_the_reference_timezone():
    # UTC 저녁 기사는 한국 날짜로 다음 날이다.
    row = article("유가 상승", stamp="2026-10-07T16:00:00+00:00")
    assert related_news(issue("유가"), [row], REFERENCE)[0]["when"] == "오늘"


def test_coverage_counts_other_articles_on_the_same_story_and_breaks_ties():
    pool = [article("유가 반등, 후티 반군 홍해 위협"), article("후티 반군 홍해 위협 고조에 해운주 강세"),
            article("유가 반등, 재고 감소")]
    rows = related_news(issue("유가"), pool, REFERENCE)
    assert rows[0]["title"] == "유가 반등, 후티 반군 홍해 위협" and rows[0]["coverage"] == 1
