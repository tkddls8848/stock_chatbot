import json
from dataclasses import replace
from datetime import date

import pytest

from polymarket_shorts import pipeline
from polymarket_shorts.config import Settings
from polymarket_shorts.media import ASSET_DIR, background_for
from polymarket_shorts.scenario import Scene, Scenario


def test_local_backgrounds_are_present_and_missing_images_use_plain_background(tmp_path):
    assert background_for("intro", "") == ASSET_DIR / "financial-city.png"
    assert background_for("consensus", "global cargo shipping containers trade") == ASSET_DIR / "global-trade.png"
    assert background_for("intro", "", tmp_path) is None
    (tmp_path / "financial-city.png").write_bytes(b"invalid png")
    assert background_for("intro", "", tmp_path) is None


@pytest.mark.parametrize("enabled", [True, False])
def test_daily_pipeline_passes_saved_backgrounds_without_network_generation(tmp_path, monkeypatch, enabled):
    scenes = (
        Scene("intro", "제목", "기준", "본문", "내레이션"),
        Scene("consensus", "무역", "기준", "본문", "내레이션", visual_query="global cargo shipping containers trade"),
    )
    scenario = Scenario("2026-09-12", "g1", "2026-09-12T00:00:00+09:00", scenes)
    monkeypatch.setattr(pipeline.PolymarketWebClient, "snapshot", lambda self: object())
    monkeypatch.setattr(pipeline, "build_scenario", lambda *a, **kw: scenario)
    monkeypatch.setattr(pipeline, "synthesize", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "probe_duration", lambda *a, **kw: 10.0)
    captured = {}

    def render(*args, **kwargs):
        captured.update(kwargs)
        kwargs["output_path"].touch()
        return 10.0

    monkeypatch.setattr(pipeline, "render_video", render)
    settings = replace(Settings.from_env(), output_dir=tmp_path / "output", state_file=tmp_path / "state.json", visuals_enabled=enabled)
    pipeline.produce_daily(settings, production_date=date(2026, 9, 12))
    expected = (ASSET_DIR / "financial-city.png", ASSET_DIR / "global-trade.png") if enabled else (None, None)
    assert captured["background_paths"] == expected
    payload = json.loads((settings.output_dir / "2026-09-12" / "scenario.json").read_text(encoding="utf-8"))
    assert [v["asset"] if v else None for v in payload["visuals"]] == [p.name if p else None for p in expected]
