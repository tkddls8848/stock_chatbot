from datetime import date

from polymarket_shorts.client import Snapshot
from polymarket_shorts.pipeline import _faster_rate, metadata_for
from polymarket_shorts.scenario import build_scenario, clip_at_sentence


def _snapshot(groups):
    return Snapshot(
        brief={
            "generation_id": "g1",
            "written_at": "2026-09-01T20:00:00+09:00",
            "groups": groups,
        },
        summary={
            "generation_id": "g1",
            "accounting": {"open_event_count": 22047},
        },
    )


def _group(key, label, volume, paragraph="첫 문장입니다. 두 번째 문장입니다."):
    return {
        "key": key,
        "label": label,
        "status": "ok",
        "event_count": 20,
        "volume24hr": volume,
        "paragraph": paragraph,
    }


def test_clip_keeps_complete_sentences():
    assert clip_at_sentence("첫 문장입니다. 두 번째 문장입니다.", 10) == "첫 문장입니다."


def test_clip_uses_remaining_space_when_next_sentence_is_long():
    text = "짧은 첫 문장입니다. " + "아주긴문장" * 20 + "."

    clipped = clip_at_sentence(text, 45)

    assert len(clipped) <= 45
    assert clipped.startswith("짧은 첫 문장입니다.")
    assert clipped.endswith("…")


def _label(scene) -> str:
    """표시용 제목에서 분야 라벨만 떼어 낸다.

    `scenario.py`가 제목에 `SIGNAL n · ` 접두사를 붙인다. 아래 테스트가 보는 것은
    어떤 분야가 어떤 순서로 뽑히는가이지 제목 서식이 아니라, 서식을 바꿔도 선정
    로직 테스트가 깨지지 않도록 라벨만 비교한다.
    """
    return scene.title.split(" · ")[-1]


def test_cards_follow_the_web_order_highest_24h_volume_first():
    """웹의 `?sort=volume24hr` 화면과 같은 순서여야 한다."""
    scenario = build_scenario(
        _snapshot(
            [
                _group("general", "기타 경제", 100),
                _group("macro", "거시", 300),
                _group("equities", "주식", 200),
                _group("composite", "복합", 1),
            ]
        ),
        production_date=date(2026, 9, 1),
    )

    labels = [_label(scene) for scene in scenario.scenes if scene.kind == "consensus"]
    assert labels == ["거시", "주식", "기타 경제", "복합"]
    assert scenario.scenes[0].kind == "intro"
    assert scenario.scenes[-1].kind == "outro"


def test_every_sector_gets_a_card_even_when_its_summary_failed():
    """요약 생성이 실패해도 카드는 세운다.

    건수와 거래량은 요약과 무관하게 늘 있다. 분야를 통째로 빼면 화면이
    "오늘은 네 분야뿐인가"로 읽힌다.
    """
    stale = {**_group("macro", "거시", 300), "stale": True}
    failed = {**_group("general", "기타", 200), "status": "failed"}
    scenario = build_scenario(
        _snapshot([stale, failed, _group("equities", "주식", 100)]),
        production_date=date(2026, 9, 1),
    )

    assert [_label(s) for s in scenario.scenes if s.kind == "consensus"] == [
        "거시",
        "기타",
        "주식",
    ]


def test_a_card_without_any_summary_says_so_instead_of_inventing_one():
    bare = {**_group("macro", "거시", 300), "paragraph": "", "overview": ""}
    scenario = build_scenario(_snapshot([bare]), production_date=date(2026, 9, 1))

    card = next(s for s in scenario.scenes if s.kind == "consensus")
    assert "준비하지 못했습니다" in card.narration


def test_each_card_carries_the_event_count_and_24h_volume():
    """사용자가 카드에서 먼저 보는 두 숫자다."""
    scenario = build_scenario(
        _snapshot([_group("macro", "거시", 20_578_090)]),
        production_date=date(2026, 9, 1),
    )

    card = next(s for s in scenario.scenes if s.kind == "consensus")
    assert "20건" in card.narration
    assert "20.6M달러" in card.narration
    assert any("이벤트" in bullet for bullet in card.bullets)
    assert any("24시간 거래량" in bullet for bullet in card.bullets)


def test_youtube_metadata_has_disclaimer_and_shorts_marker():
    scenario = build_scenario(
        _snapshot([_group("macro", "거시", 300)]),
        production_date=date(2026, 9, 1),
    )

    metadata = metadata_for(scenario)

    assert "#Shorts" in metadata["title"]
    assert "투자 조언이 아닙니다" in metadata["description"]
    assert len(metadata["title"]) <= 100


def test_tts_rate_is_increased_only_as_much_as_needed():
    assert _faster_rate("-4%", actual=190, target=177) == "+5%"


def test_long_summaries_are_cut_at_a_clause_not_mid_word():
    """그냥 자르면 카드에 '우세한 방향이 나…'처럼 단어 한가운데가 남는다."""
    text = "전체적으로 전망이 금리 정책과 경제 지표에 집중되어 있으며, 일부 질문에서는 우세한 방향이 나타난다."

    clipped = clip_at_sentence(text, 45)

    assert clipped.endswith("…")
    assert not clipped.rstrip("…").endswith(" ")
    # 잘린 자리가 어절 경계여야 한다 — 마지막 토막이 통째로 남는다.
    last = clipped.rstrip("…").split()[-1]
    assert last in {word.strip(",.") for word in text.split()}


def test_card_bullets_use_the_label_separator_the_renderer_expects():
    """렌더러가 " · "로 라벨과 값을 나눈다. 값 안에 그 구분자를 넣으면 잘린다."""
    scenario = build_scenario(
        _snapshot([_group("macro", "거시", 300)]), production_date=date(2026, 9, 1)
    )

    card = next(s for s in scenario.scenes if s.kind == "consensus")
    for bullet in card.bullets:
        label, separator, value = bullet.partition(" · ")
        assert separator, f"라벨이 없다: {bullet}"
        assert " · " not in value, f"값 안에 구분자가 또 있다: {bullet}"
