import json

import pytest

from polymarket_shorts import highlights
from polymarket_shorts.config import Settings
from polymarket_shorts.highlights import HighlightError, validate_scripts, validate_selection


def selection(identity="e1", **changes):
    return {"id": identity, "source_title": "Fed Decision in October?", "relevance": 3, "timeliness": 2, "topic": "Fed Decision",
            "reason": "금리 결정은 자금조달 비용에 영향을 주므로 발표 조건을 확인할 필요가 있습니다.", **changes}


def test_selection_can_leave_sectors_empty(issue_source):
    candidate = issue_source[1]
    assert validate_selection({"selected": []}, [candidate], 5) == []
    assert validate_selection({"selected": [selection()]}, [candidate], 5)[0]["id"] == "e1"


@pytest.mark.parametrize("changes", [{"id": "invented"}, {"relevance": 1}, {"timeliness": True}, {"reason": "짧음"}])
def test_invalid_or_weak_selection_stops(issue_source, changes):
    with pytest.raises(HighlightError):
        validate_selection({"selected": [selection(**changes)]}, [issue_source[1]], 5)


def test_duplicate_sector_and_cross_sector_topic_are_rejected(issue_source):
    candidate = issue_source[1]
    for extra in ({"id": "e2", "topic_key": "other"}, {"id": "e2", "sector": "general"}):
        rejected = []
        result = validate_selection({"selected": [selection(), selection("e2", topic="October")]},
                                    [candidate, {**candidate, **extra}], 5, rejected=rejected)
        assert len(result) == 1 and rejected[0]["id"] == "e2"


def test_script_translation_accepts_calendar_month_and_exact_market_numbers(issue_source):
    issue, script = issue_source[3:]
    # 배경 묘사가 없으면 None으로 남고 분야 기본 장면으로 그린다.
    assert validate_scripts({"scripts": [script]}, [issue]) == [{**script, "image_scene": None}]


@pytest.mark.parametrize("change", [
    {"context": "금리 인상 확률은 80%입니다."},
    {"context": "연준 회의가 다가오는데, 이는 금리 경로로 이어집니다."},   # 지시어로 앞 문장을 받는다
    {"market_labels": [{"id": "m2", "label": "금리 동결"}, {"id": "m1", "label": "금리 인상"}]},
    {"market_labels": [{"id": "m1", "label": "25bp 금리 동결"}, {"id": "m2", "label": "금리 인상"}]},
])
def test_unbacked_numbers_and_mismatched_market_references_stop(issue_source, change):
    issue, script = issue_source[3:]
    with pytest.raises(HighlightError):
        validate_scripts({"scripts": [{**script, **change}]}, [issue])


def test_model_calls_only_receive_shortlist_then_selected_market_questions(issue_source, monkeypatch):
    _, candidate, _, issue, script = issue_source
    calls = []
    def chat(settings, **kwargs):
        calls.append(json.loads(kwargs["user"]))
        return {"selected": [selection()]} if len(calls) == 1 else {"scripts": [script]}
    monkeypatch.setattr(highlights, "chat_json", chat)
    highlights.select_issues([candidate], Settings.from_env())
    highlights.write_issues([issue], Settings.from_env())
    assert len(calls) == 2
    assert calls[0]["candidates"][0]["id"] == candidate["id"]
    assert calls[1][0]["markets"][0] == {"id": "m1", "question": issue["markets"][0]["question"]}


def test_question_percentages_are_preserved_but_betting_percentages_cannot_be_invented():
    highlights._translation("물가 3.4% 초과", "Inflation above 3.4%?", "label")
    with pytest.raises(HighlightError):
        highlights._translation("물가 3.4% 초과", "Inflation above 3.4?", "label")


def test_wrong_subject_attached_to_valid_event_id_is_rejected(issue_source):
    rejected = []
    assert validate_selection({"selected": [selection(topic="Crude Oil")]}, [issue_source[1]], 5, rejected=rejected) == []
    assert rejected[0]["id"] == "e1"


@pytest.mark.parametrize("text", ["9월 원유 하락", "9월 원유 90달러 도달", "9월 원유 90달러 이상"])
def test_market_label_cannot_omit_threshold_or_reverse_direction(text):
    with pytest.raises(HighlightError):
        highlights._translation(text, "Will WTI hit (LOW) $90 in September?", "label")


def test_invented_news_references_are_dropped_not_fatal(issue_source):
    """뉴스 번호는 검수 기록의 보조 근거라 지어낸 번호만 버리고 원고는 살린다."""
    issue, script = issue_source[3:]
    real = issue["news"][0]["id"] if issue["news"] else None
    ids = ["news:invented"] + ([real, real] if real else [])
    (clean,) = validate_scripts({"scripts": [{**script, "news_ids": ids}]}, [issue])
    assert clean["news_ids"] == ([real] if real else [])


def test_a_duplicated_label_id_is_collapsed(issue_source):
    issue, script = issue_source[3:]
    labels = script["market_labels"]
    (clean,) = validate_scripts({"scripts": [{**script, "market_labels": [labels[0], *labels]}]}, [issue])
    assert [label["id"] for label in clean["market_labels"]] == [m["id"] for m in issue["markets"]]


def test_image_scene_is_kept_when_well_formed_and_dropped_otherwise(issue_source):
    issue, script = issue_source[3:]
    good = "oil tankers crossing a narrow sea strait at dusk"
    (clean,) = validate_scripts({"scripts": [{**script, "image_scene": good}]}, [issue])
    assert clean["image_scene"] == good
    for bad in ("해협", "ships", 42):
        (clean,) = validate_scripts({"scripts": [{**script, "image_scene": bad}]}, [issue])
        assert clean["image_scene"] is None


def test_image_scene_that_invites_drawn_text_is_dropped(issue_source):
    issue, script = issue_source[3:]
    risky = "financial charts with rising interest rate indicators over a city"
    (clean,) = validate_scripts({"scripts": [{**script, "image_scene": risky}]}, [issue])
    assert clean["image_scene"] is None
    safe = "a harbor with many container ships at dusk and cranes"   # many·harbor는 걸리지 않는다
    (clean,) = validate_scripts({"scripts": [{**script, "image_scene": safe}]}, [issue])
    assert clean["image_scene"] == safe


@pytest.mark.parametrize(("outlook", "kept"), [
    ("WTI가 9월에 90달러까지 내릴", True),
    ("WTI가 9월에 90달러까지 내릴 것", False),     # "것"은 프로그램이 붙인다
    ("WTI가 9월에 90달러까지 내린다", False),      # 관형형이 아니면 "것으로 봅니다"가 붙지 않는다
    ("WTI가 9월에 90달러 이상으로 오를", False),   # LOW 질문의 방향을 뒤집었다
    ("WTI가 9월에 하락할", False),                 # 기준 수치를 뺐다
])
def test_outlook_must_be_an_adnominal_clause_with_the_same_numbers_and_direction(outlook, kept):
    """음성은 "참여자의 N%는 ⟨outlook⟩ 것으로 봅니다"다(운영자 결정 2026-10-02)."""
    result = highlights._outlook(outlook, "Will WTI hit (LOW) $90 in September?", set())
    assert (result == outlook) is kept


def test_a_bad_outlook_is_dropped_without_failing_the_script(issue_source):
    """전망 구절이 틀려도 그날 원고를 버리지 않는다. 음성은 라벨로 같은 틀을 만든다."""
    issue, script = issue_source[3:]
    labels = [{**label, "outlook": "틀린 것"} for label in script["market_labels"]]
    (clean,) = validate_scripts({"scripts": [{**script, "market_labels": labels}]}, [issue])
    assert all("outlook" not in label for label in clean["market_labels"])


def test_scene_opener_is_kept_when_clean_and_dropped_without_failing_the_script(issue_source):
    """장면 여는 말(`lead_in`)은 틀려도 원고를 다시 묻지 않는다 — 대체 문장(`speech.transition`)이 있다."""
    _, _, _, issue, script = issue_source
    good = "먼저 연준의 금리 결정부터 보겠습니다."
    (clean,) = validate_scripts({"scripts": [{**script, "lead_in": good}]}, [issue])
    assert clean["lead_in"] == good
    for bad in ("확률이 80%인 질문입니다.", "짧음", 123):
        (clean,) = validate_scripts({"scripts": [{**script, "lead_in": bad}]}, [issue])
        assert "lead_in" not in clean


def test_selection_keeps_specific_subject_keywords_and_drops_generic_ones(issue_source):
    _, candidate, _, _, _ = issue_source
    payload = {"selected": [selection(keywords=["연준", "금리", "FOMC", "연준", "x", 5, "아주아주아주긴주체어입니다만", "에너지"])]}
    (chosen,) = validate_selection(payload, [candidate], 5)
    assert chosen["selection"]["keywords"] == ["연준", "FOMC"]
    (chosen,) = validate_selection({"selected": [selection(keywords="연준")]}, [candidate], 5)
    assert chosen["selection"]["keywords"] == []


def _with_market_news(issue):
    return {**issue, "selection": {**issue.get("selection", {}), "keywords": ["연준", "Fed"]}, "market_news": [{"id": "market:1", "title": "연준 위원, 10월 금리 동결 지지 발언",
                                      "original": "Fed official backs October hold", "when": "어제", "coverage": 2}]}


def test_news_hook_is_kept_when_it_reports_the_chosen_article(issue_source):
    _, _, _, issue, script = issue_source
    hook = "어제는 연준 위원이 10월 금리 동결을 지지했다는 보도가 나왔습니다."
    (clean,) = validate_scripts({"scripts": [{**script, "news_hook": hook, "hook_news_id": "market:1"}]},
                                [_with_market_news(issue)])
    assert (clean["news_hook"], clean["hook_news_id"]) == (hook, "market:1")


@pytest.mark.parametrize("hook, chosen", [
    ("어제는 유럽 증시가 반등했다는 소식이 전해졌습니다.", "market:1"),                  # 기사가 전하지 않는 말
    ("", "market:1"),
    ("어제 연준 위원이 3명이나 동결을 지지했다는 보도가 나왔습니다.", "market:1"),        # 기사에 없는 숫자
    ("이 소식에 참여자들의 동결 기대가 높아졌다는 보도가 나왔습니다.", "market:1"),        # 전망과의 인과
    ("어제 동결 지지 발언 뒤 동결 확률이 올랐다는 보도가 나왔습니다.", "market:1"),
    ("짧음", "market:1"),
    ("어제는 유럽중앙은행 위원이 10월 금리 동결을 지지했다는 보도가 나왔습니다.", "market:1"),  # 다른 주체
])
def test_a_bad_news_hook_is_dropped_without_failing_the_script(issue_source, hook, chosen):
    _, _, _, issue, script = issue_source
    (clean,) = validate_scripts({"scripts": [{**script, "news_hook": hook, "hook_news_id": chosen}]},
                                [_with_market_news(issue)])
    assert "news_hook" not in clean and "hook_news_id" not in clean
    assert clean["hook_note"] == (f"검사에서 제외: {hook}" if hook else "모델이 맞는 기사가 없다고 봄")


@pytest.mark.parametrize("cited", ["market:9", None, "market:2"])
def test_a_news_hook_is_attributed_to_the_candidate_it_actually_reports(issue_source, cited):
    """id를 잘못 적어도 문장이 전하는 후보로 되돌린다 — 검수 근거가 다른 기사를 가리키면 안 된다."""
    _, _, _, issue, script = issue_source
    issue = _with_market_news(issue)
    issue["market_news"].append({"id": "market:2", "title": "유로존 주식, 고금리에도 상승 기대",
                                 "original": "European stocks seen higher", "when": "오늘", "coverage": 0})
    hook = "어제는 연준 위원이 10월 금리 동결을 지지했다는 보도가 나왔습니다."
    (clean,) = validate_scripts({"scripts": [{**script, "news_hook": hook, "hook_news_id": cited}]}, [issue])
    assert clean["hook_news_id"] == "market:1"


def test_a_single_choice_label_missing_only_the_year_gets_it_from_the_question(issue_source):
    """"Another Fed rate hike in 2026?"을 "추가 인상"으로 옮겨 그날 첫 제작이 실패했다(2026-10-08)."""
    _, _, _, issue, script = issue_source
    issue = {**issue, "markets": [{**issue["markets"][0], "question": "Another Fed rate hike in 2026?"}]}
    row = {**script, "market_labels": [{"id": issue["markets"][0]["id"], "label": "추가 인상"}]}
    (clean,) = validate_scripts({"scripts": [row]}, [issue])
    assert clean["market_labels"][0]["label"] == "2026년 추가 인상"


def test_a_bad_watch_point_does_not_drop_the_issue(issue_source):
    _, _, _, issue, script = issue_source
    (clean,) = validate_scripts({"scripts": [{**script, "watch_point": "9월의 공식 결정을 확인하세요."}]}, [issue])
    assert clean["watch_point"] == "" and clean["context"] == script["context"]


def test_an_etf_price_question_must_name_the_etf_not_the_index(issue_source):
    """"S&P 500 (SPY) closes above $750"을 "S&P 500이 750달러 위로"로 옮기면 지수 이야기로 들린다(운영자 지적 2026-10-08)."""
    _, _, _, issue, script = issue_source
    issue = {**issue, "title": "S&P 500 (SPY) closes above ___ on October 8?"}
    for row in ({**script, "headline": "S&P 500 마감가"},
                {**script, "question": "S&P 500이 10월에 금리를 어떻게 결정할까요?"}):
        with pytest.raises(HighlightError, match="SPY"):
            validate_scripts({"scripts": [row]}, [issue])
    (clean,) = validate_scripts({"scripts": [{**script, "headline": "S&P 500 ETF(SPY) 종가",
                                              "question": "SPY는 10월에 금리를 어떻게 결정할까요?"}]}, [issue])
    assert "SPY" in clean["headline"]


@pytest.mark.parametrize(("title", "topic", "kept"), [
    ("Will the U.S. invade Iran before 2027?", "US Iran invasion", True),    # 같은 어간·점 표기
    ("Will the U.S. invade Iran before 2027?", "U.S. Iran", True),
    ("Will the U.S. invade Iran before 2027?", "Iran nuclear deal", False),  # 제목에 없는 사건
    ("Crude Oil all time high by...?", "Gold price", False),
])
def test_topic_may_use_another_form_of_a_title_word_but_not_another_subject(issue_source, title, topic, kept):
    candidate = {**issue_source[1], "title": title}
    row = selection(source_title=title, topic=topic)
    assert bool(validate_selection({"selected": [row]}, [candidate], 5)) is kept
