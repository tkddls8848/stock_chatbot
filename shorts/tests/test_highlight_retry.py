"""의미 검증 실패는 사유를 붙여 한 번만 다시 묻고, 잘린 응답은 다시 묻지 않는다."""

import pytest

from polymarket_shorts import highlights
from polymarket_shorts.config import Settings
from polymarket_shorts.highlights import HighlightError
from polymarket_shorts.llm import TruncatedError


def _run(monkeypatch, replies):
    calls = []

    def chat(settings, **kwargs):
        calls.append(kwargs["user"])
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(highlights, "chat_json", chat)

    def check(payload):
        if not payload.get("ok"):
            raise HighlightError("watch_point에 원문에 없는 숫자가 있습니다")
        return payload

    result = highlights._ask_checked(Settings.from_env(), system="s", user="u", max_tokens=10, check=check)
    return result, calls


def test_first_valid_answer_costs_one_call(monkeypatch):
    result, calls = _run(monkeypatch, [{"ok": True}])
    assert result == {"ok": True} and len(calls) == 1


def test_invalid_answer_is_corrected_once_with_the_reason(monkeypatch):
    result, calls = _run(monkeypatch, [{"ok": False}, {"ok": True}])
    assert result == {"ok": True} and len(calls) == 2
    assert "watch_point에 원문에 없는 숫자" in calls[1] and '"ok": false' in calls[1]


def test_second_failure_stops(monkeypatch):
    with pytest.raises(HighlightError):
        _run(monkeypatch, [{"ok": False}, {"ok": False}, {"ok": True}])


def test_truncated_answer_is_not_retried(monkeypatch):
    with pytest.raises(TruncatedError):
        _run(monkeypatch, [TruncatedError("cut"), {"ok": True}])


def test_watch_point_may_cite_a_number_from_the_description():
    issue = {"id": "1", "title": "Strait traffic normal?", "description": "Resolves on the 7-day moving average.",
             "markets": [], "news": []}
    row = {"id": "1", "headline": "해협 통행 정상화", "question": "해협 통행이 정상화될까요?",
           "context": "해협 교통량은 국제 통상에 영향을 줍니다.", "watch_point": "포트워치의 7일 이동 평균을 확인하세요.",
           "market_labels": [], "news_ids": []}
    assert highlights.validate_scripts({"scripts": [row]}, [issue])[0]["watch_point"].startswith("포트워치")
    # 확인점은 검수 기록에만 남는다. 원문에 없는 숫자를 쓰면 확인점만 비우고 이슈는 살린다(2026-10-08).
    (clean,) = highlights.validate_scripts({"scripts": [{**row, "watch_point": "포트워치의 30일 평균을 확인하세요."}]}, [issue])
    assert clean["watch_point"] == "" and clean["context"]


def test_all_content_errors_are_reported_together():
    issue = {"id": "1", "title": "Fed Decision in October?", "description": "",
             "markets": [{"id": "m1", "question": "Will the Fed cut by 25 bps in October?"},
                         {"id": "m2", "question": "No change in October?"}], "news": []}
    row = {"id": "1", "headline": "연준 10월 결정", "question": "연준은 10월에 어떻게 할까요?",
           "context": "금리 결정은 자금조달 비용과 연결됩니다.", "watch_point": "연준의 결정문을 확인하세요.",
           "market_labels": [{"id": "m1", "label": "금리 인하"}, {"id": "m2", "label": "10월 60 동결"}],
           "news_ids": []}
    with pytest.raises(HighlightError) as caught:
        highlights.validate_scripts({"scripts": [row]}, [issue])
    message = str(caught.value)
    # 두 라벨의 서로 다른 오류가 한 번에 나와야 교정 한 번으로 둘 다 고친다.
    # 10월은 질문에서 채우고, 남은 25bp만 교정 대상으로 알린다.
    assert "m1" in message and "빠졌습니다(25)" in message
    assert "m2" in message and "원문에 없는 숫자가 있습니다(60)" in message


def test_a_label_missing_only_the_deadline_gets_it_from_the_question():
    issue = {"id": "1", "title": "Strait of Hormuz traffic returns to normal by December 31?", "description": "",
             "markets": [{"id": "m1", "question": "Strait of Hormuz traffic returns to normal by December 31?"}],
             "news": []}
    row = {"id": "1", "headline": "호르무즈 해협 교통", "question": "호르무즈 해협 교통이 12월 31일까지 정상화될까요?",
           "context": "해협 교통량은 국제 통상에 영향을 줍니다.", "watch_point": "해운 당국 발표를 확인하세요.",
           "market_labels": [{"id": "m1", "label": "해협 교통 정상화"}], "news_ids": []}
    (clean,) = highlights.validate_scripts({"scripts": [row]}, [issue])
    assert clean["market_labels"][0]["label"] == "12월 31일까지 해협 교통 정상화"


def test_other_label_errors_are_not_papered_over():
    assert highlights._with_question_date("금리 인상", "Will the Fed hike by 25 bps?") is None
    assert highlights._with_question_date("10월 금리 인상", "Fed decision in October?") is None


def test_number_words_and_amount_units_count_as_grounded():
    source = "Will the variant token FDV be above $100M one day after launch?"
    known = highlights._numbers(source)
    assert {"1", "100"} <= known                         # one → 1, $100M → 1억
    highlights._translation("변이형 토큰의 FDV가 출시 1일 후에 1억 달러를 넘을까요?", source, "question")
    assert "25" in highlights._numbers("above $2.5B?")   # 25억
    assert "5000" in highlights._numbers("$50M")         # 5000만


def test_a_label_may_use_either_form_of_an_amount():
    question = "Variational FDV above $100M one day after launch?"
    highlights._translation("출시 1일 후 FDV 1억 달러 초과", question, "label")
    highlights._translation("출시 1일 후 FDV 100M 달러 초과", question, "label")
    with pytest.raises(HighlightError):
        highlights._translation("출시 직후 FDV 초과", question, "label")      # 수치 조건이 모두 빠졌다


def test_one_issue_that_stays_wrong_is_dropped_and_the_rest_are_kept(monkeypatch):
    good = {"id": "1", "title": "Fed decision?", "description": "", "news": [],
            "markets": [{"id": "m1", "question": "Fed cut?"}]}
    bad = {"id": "2", "title": "Token FDV above $300M one day after launch?", "description": "", "news": [],
           "markets": [{"id": "m2", "question": "Token FDV above $300M one day after launch?"}]}

    def row(issue, label):
        return {"id": issue["id"], "headline": "연준 결정 이슈", "question": "연준이 금리를 내릴까요?",
                "context": "금리 결정은 자금조달 비용과 연결됩니다.", "watch_point": "연준의 결정문을 확인하세요.",
                "market_labels": [{"id": issue["markets"][0]["id"], "label": label}], "news_ids": []}

    reply = {"scripts": [row(good, "금리 인하"), row(bad, "3억 달러 이상")]}   # 둘째는 '1일 후'가 빠졌다
    monkeypatch.setattr(highlights, "chat_json", lambda settings, **kwargs: reply)
    scripts = highlights.write_issues([good, bad], Settings.from_env())
    assert [script["id"] for script in scripts] == ["1"]
