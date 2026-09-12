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


def test_scenario_keeps_composite_then_highest_volume_groups():
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
        max_groups=3,
    )

    labels = [_label(scene) for scene in scenario.scenes if scene.kind == "consensus"]
    assert labels == ["복합", "거시", "주식"]
    assert scenario.scenes[0].kind == "intro"
    assert scenario.scenes[-1].kind == "outro"


def test_scenario_excludes_stale_or_failed_paragraphs():
    stale = {**_group("macro", "거시", 300), "stale": True}
    failed = {**_group("general", "기타", 200), "status": "failed"}
    scenario = build_scenario(
        _snapshot([stale, failed, _group("equities", "주식", 100)]),
        production_date=date(2026, 9, 1),
    )

    assert [_label(s) for s in scenario.scenes if s.kind == "consensus"] == ["주식"]


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
