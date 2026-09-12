from dataclasses import replace
from datetime import date

from polymarket_shorts.config import Settings
from polymarket_shorts.pipeline import produce_daily


def test_daily_state_prevents_a_second_production(tmp_path):
    state_file = tmp_path / "state" / "published.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        '{"days":{"2026-09-01":{"video_path":"already.mp4","youtube_id":"abc"}}}',
        encoding="utf-8",
    )
    settings = replace(
        Settings.from_env(),
        output_dir=tmp_path / "output",
        state_file=state_file,
    )

    result = produce_daily(settings, production_date=date(2026, 9, 1))

    assert result.status == "already_produced"
    assert result.video_path == "already.mp4"
    assert result.youtube_id == "abc"
