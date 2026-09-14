"""분야 문단에서 뽑은 이슈는 문단에 있는 것만으로 이뤄져야 한다.

모델이 고르는 것은 "무엇을 말할까"이지 "무엇이 사실인가"가 아니다. 여기 테스트는
문단에 없는 수치가 멘트로 새어 나가는 길을 전부 막는다 — 그게 뚫리면 영상은
폴리마켓이 매기지 않은 확률을 폴리마켓의 값처럼 읽는다.
"""

from dataclasses import replace
import json

import pytest

from polymarket_shorts import highlights, llm
from polymarket_shorts.config import Settings
from polymarket_shorts.highlights import HighlightError, pick_highlights, validate


PARAGRAPH = (
    "전체적으로 질문별 전망의 차이가 커 하나의 방향으로 묶기 어렵다. "
    "호르무즈 해협 교통이 정상화될 가능성은 17.5%로 낮게 나타나며, "
    "중국이 비트코인을 해제할 가능성은 2.35%에 그친다."
)
MACRO = (
    "전체적으로 전망이 금리 정책에 집중되어 있다. "
    "연준의 금리 인상 가능성은 88.5%로 나타났다."
)


def _cards():
    return [
        {"key": "composite", "label": "복합", "event_count": 8, "volume24hr": 121228.6,
         "strong": 3, "tight": 0, "paragraph": PARAGRAPH},
        {"key": "macro", "label": "거시·통화", "event_count": 103, "volume24hr": 8172840.0,
         "strong": 15, "tight": 18, "paragraph": MACRO},
    ]


def _payload(**overrides):
    picks = [
        {"key": "composite", "headline": "호르무즈 정상화 17.5%",
         "caption": "호르무즈 해협 교통 정상화 가능성은 17.5%에 그친다.",
         "narration": "호르무즈 해협 교통이 정상화될 가능성은 17.5%로 낮게 나타났다. "
                      "중국이 비트코인을 해제할 가능성은 2.35%에 그친다."},
        {"key": "macro", "headline": "연준 인상 88.5%",
         "caption": "연준의 금리 인상 가능성은 88.5%로 나타났다.",
         "narration": "연준의 금리 인상 가능성은 88.5%로 나타났다. "
                      "금리 결정에 질문이 몰려 있다."},
    ]
    return {"hook": "호르무즈 해협 정상화 가능성은 17.5%로 나타났다.", "picks": picks, **overrides}


def _validated(payload, cards=None):
    return validate(payload, cards or _cards(), low=40, high=160)


def test_picked_lines_are_spoken_in_polite_korean():
    picked = _validated(_payload())

    assert picked.hook.endswith("나타났습니다.")
    assert picked.picks["composite"].narration.endswith("그칩니다.")
    assert picked.picks["macro"].caption == "연준의 금리 인상 가능성은 88.5%로 나타났습니다."
    assert picked.picks["composite"].headline == "호르무즈 정상화 17.5%"


def test_a_number_that_is_not_in_the_paragraph_is_refused():
    """반올림도 지어낸 것이다. 17.5%를 18%로 읽으면 화면 수치와 말이 갈라진다."""
    payload = _payload()
    payload["picks"][0]["narration"] = (
        "호르무즈 해협 교통이 정상화될 가능성은 18%로 낮게 나타났습니다. "
        "중국이 비트코인을 해제할 가능성은 2.35%에 그칩니다."
    )

    with pytest.raises(HighlightError, match="18"):
        _validated(payload)


def test_a_number_borrowed_from_another_sector_is_refused():
    """문단이 다르면 근거도 다르다. 거시의 88.5%를 복합 장면에서 읽을 수 없다."""
    payload = _payload()
    payload["picks"][0]["narration"] = (
        "복합 분야에서 금리 인상 가능성은 88.5%로 나타났습니다. "
        "중국이 비트코인을 해제할 가능성은 2.35%에 그칩니다."
    )

    with pytest.raises(HighlightError, match="88.5"):
        _validated(payload)


def test_a_generic_opening_is_refused():
    """어느 분야에 갖다 놔도 말이 되는 문장은 아무것도 말하지 않는다."""
    payload = _payload()
    payload["picks"][0]["narration"] = (
        "전체적으로 질문별 전망의 차이가 커 하나의 방향으로 묶기 어렵습니다. "
        "호르무즈 해협 정상화 가능성은 17.5%입니다."
    )

    with pytest.raises(HighlightError, match="총론"):
        _validated(payload)


def test_the_screen_separator_cannot_enter_a_picked_line():
    """렌더러가 화면 항목을 ' · '로 나눈다. 값 안에 들어가면 잘린다."""
    payload = _payload()
    payload["picks"][0]["caption"] = "호르무즈 해협 · 정상화 가능성은 17.5%다."

    with pytest.raises(HighlightError, match="구분자"):
        _validated(payload)


@pytest.mark.parametrize(
    ("broken", "match"),
    [
        ({"hook": "짧다"}, "hook"),
        ({"picks": []}, "분야"),
        ({"summary": "덤"}, "hook과 picks"),
    ],
)
def test_missing_or_extra_fields_are_refused(broken, match):
    with pytest.raises(HighlightError, match=match):
        _validated(_payload(**broken))


def test_an_unknown_or_repeated_sector_is_refused():
    payload = _payload()
    payload["picks"][1]["key"] = "composite"

    with pytest.raises(HighlightError, match="중복"):
        _validated(payload)


def test_every_sector_needs_a_pick():
    payload = _payload()
    payload["picks"] = payload["picks"][:1]

    with pytest.raises(HighlightError, match="분야 2개"):
        _validated(payload)


def test_request_sends_the_paragraphs_and_reads_back_picks(monkeypatch):
    settings = replace(
        Settings.from_env(), editor_account_id="account", editor_api_token="secret",
    )
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"content": json.dumps(_payload())}}]}

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr(llm.requests, "post", post)
    groups = [
        {"key": "composite", "label": "복합", "status": "ok", "event_count": 8,
         "volume24hr": 121228.6, "paragraph": PARAGRAPH},
        {"key": "macro", "label": "거시·통화", "status": "ok", "event_count": 103,
         "volume24hr": 8172840.0, "paragraph": MACRO},
    ]

    picked = pick_highlights(groups, settings, target_chars=340)

    assert "account" in captured["url"]
    sent = json.loads(captured["messages"][1]["content"])
    assert [card["key"] for card in sent] == ["composite", "macro"]
    assert sent[0]["paragraph"] == PARAGRAPH
    assert set(picked.picks) == {"composite", "macro"}


def test_a_sector_without_a_paragraph_is_not_sent_to_the_model(monkeypatch):
    """문단이 없으면 고를 것도 없다. 빈 입력으로 Neurons를 태우지 않는다."""
    monkeypatch.setattr(
        highlights, "chat_json", lambda *a, **kw: pytest.fail("문단 없이 호출하면 안 된다"),
    )

    with pytest.raises(HighlightError, match="문단이 있는 분야가 없습니다"):
        pick_highlights(
            [{"key": "macro", "label": "거시", "paragraph": ""}],
            Settings.from_env(), target_chars=760,
        )


def test_a_headline_without_a_number_is_refused_when_the_paragraph_has_one():
    """수치를 뺀 제목은 "금리 인상"처럼 분류 이름으로 돌아간다."""
    payload = _payload()
    payload["picks"][1]["headline"] = "금리 인상"

    with pytest.raises(HighlightError, match="수치가 없습니다"):
        _validated(payload)


def test_a_sector_whose_paragraph_has_no_number_may_be_named_without_one():
    """문단에 수치가 없으면 지어내는 대신 이름만으로 말한다."""
    cards = _cards()
    cards[1]["paragraph"] = "연준의 금리 결정에 질문이 몰려 있으며 인상 쪽에 무게가 실린다."
    payload = _payload()
    payload["picks"][1].update(
        headline="연준 인상에 무게",
        caption="연준의 금리 결정에 질문이 몰려 있다.",
        narration="연준의 금리 결정에 질문이 몰려 있으며 인상 쪽에 무게가 실린다. "
                  "결정 시점을 함께 확인한다.",
    )

    picked = _validated(payload, cards)

    assert picked.picks["macro"].headline == "연준 인상에 무게"
