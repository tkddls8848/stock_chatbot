from datetime import date

import pytest

from polymarket_shorts.pipeline import metadata_for
from polymarket_shorts.scenario import build_scenario


def test_video_uses_individual_questions_and_preserves_probabilities(issue_source):
    snapshot, _, _, issue, script = issue_source
    scenario = build_scenario(snapshot, [issue], [script], production_date=date(2026, 9, 23))
    assert len(scenario.scenes) == 3  # Missing sectors are not filled with summaries.
    scene = scenario.scenes[1]
    assert scene.body == "10월 금리 동결: 예 55%, 아니오 45%\n10월 금리 25bp 인상: 예 40%, 아니오 60%"
    assert "55%" in scene.narration and "40%" in scene.narration
    assert scene.probability == .55 and scene.volume_share == 0
    assert scene.market_ids == ("m1", "m2")
    assert scene.source_url in " ".join(scene.evidence)
    assert "개별 판정 시각과 다를 수 있음" in " ".join(scene.evidence)
    assert scenario.source_written_at == snapshot.summary["generated_at"]
    meta = metadata_for(scenario)
    assert script["headline"] in meta["title"]
    assert scene.source_url in meta["description"]


def test_wrong_market_association_is_rejected_before_render(issue_source):
    snapshot, _, _, issue, script = issue_source
    script["market_labels"].reverse()
    with pytest.raises(ValueError, match="확률"):
        build_scenario(snapshot, [issue], [script], production_date=date(2026, 9, 23))
