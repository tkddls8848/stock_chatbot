import json
from dataclasses import replace

import pytest

from polymarket_shorts import hyperframes_export
from polymarket_shorts.config import Settings

from polymarket_shorts.hyperframes_export import (
    TimedScene,
    _index_html,
    _presentation_narration,
    parse_vtt,
)


def test_consensus_narration_uses_structured_bullets_not_broken_body():
    scene = {
        "kind": "consensus",
        "title": "SIGNAL 1 · 거시·통화",
        "body": "잘린 원문 14.",
        "bullets": [
            "판단 · 방향성은 아직 열려 있습니다",
            "근거 · 101 EVENT / 24H 11.0M달러",
            "분포 · 강한 합의 15 / 경합 16",
            "체크 · 자금조달 비용을 확인하십시오",
        ],
    }

    narration = _presentation_narration(scene, 1)

    assert "잘린 원문" not in narration
    assert "101 EVENT" in narration
    assert "자금조달 비용" in narration


def test_vtt_and_vertical_composition_are_generated():
    cues = parse_vtt(
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n첫 문장\n\n"
        "00:00:01.500 --> 00:00:03.000\n두 번째 문장\n"
    )
    scenario = {
        "date": "2026-09-05",
        "scenes": [
            {
                "kind": "intro",
                "title": "오늘의 컨센서스",
                "kicker": "EXECUTIVE BRIEF",
                "bullets": ["핵심 신호 3개"],
            }
        ],
    }
    markup = _index_html(
        scenario,
        [TimedScene(0, 3, "assets/voice-01.mp3", cues)],
        3,
    )

    assert 'data-width="1080"' in markup
    assert 'data-height="1920"' in markup
    assert 'src="assets/voice-01.mp3"' in markup
    assert "첫 문장" in markup
    assert 'window.__timelines["main"]' in markup


@pytest.mark.parametrize("enabled", [True, False])
def test_export_copies_reusable_background_and_records_portable_path(tmp_path, monkeypatch, enabled):
    source = tmp_path / "scenario.json"
    source.write_text(json.dumps({"scenes": [{"kind": "intro", "title": "제목", "narration": "설명", "background_asset": "https://untrusted.invalid/old.png"}]}), encoding="utf-8")

    def synthesize(*args, **kwargs):
        kwargs["audio_path"].touch()
        kwargs["subtitle_path"].write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n설명\n", encoding="utf-8")

    monkeypatch.setattr(hyperframes_export, "synthesize", synthesize)
    monkeypatch.setattr(hyperframes_export, "probe_duration", lambda *a, **kw: 1.0)
    project = tmp_path / "project"
    manifest = hyperframes_export.export_project(source, project, settings=replace(Settings.from_env(), visuals_enabled=enabled))
    markup = (project / "index.html").read_text(encoding="utf-8")
    assert "untrusted.invalid" not in markup
    assert (project / "assets" / "financial-city.png").exists() == enabled
    assert manifest["scenes"][0]["background"] == ("assets/financial-city.png" if enabled else None)
    assert ('src="assets/financial-city.png"' in markup) == enabled
