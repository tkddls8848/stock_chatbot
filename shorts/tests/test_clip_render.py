from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from polymarket_shorts import clip_render, clips, render
from polymarket_shorts.config import Settings
from polymarket_shorts.scenario import Scenario, Scene
from polymarket_shorts.tts import Word

from conftest import requires_cjk_font


def _scenario():
    return Scenario("2026-09-26", "g1", "fixed", (
        Scene("consensus", "이슈", "", "설명", "멘트", visual_query="topic: harbor"),
        Scene("outro", "마무리", "", "고지", "마무리"),
    ))


def _fake_run(command, **kwargs):
    Path(command[-1]).touch()
    return SimpleNamespace(returncode=0, stdout="", stderr="")


@requires_cjk_font
@pytest.mark.parametrize("mode", ["disabled", "no_key", "no_clips"])
def test_still_path_preserves_exact_ffmpeg_arguments(tmp_path, monkeypatch, cjk_font, mode):
    scenario = _scenario()
    settings = replace(Settings.from_env(), generated_clips=mode != "disabled",
                       video_api_key="" if mode == "no_key" else "test-key", visuals_enabled=True)
    png = tmp_path / (hashlib.sha1(scenario.scenes[0].visual_query.encode()).hexdigest()[:12] + ".png")
    png.touch()
    monkeypatch.setattr(clips, "_generate_clip", lambda *args: None)
    selected = clips.clips_for(scenario.scenes, (png, None), settings)
    assert selected == (png, None)
    monkeypatch.setattr(clip_render, "render_video", lambda *a, **k: pytest.fail("new renderer invoked"))
    monkeypatch.setattr(render, "probe_duration", lambda *a, **k: 2)
    monkeypatch.setattr(render, "render_frame", lambda scene, path, **k: path.touch())
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return _fake_run(command, **kwargs)
    monkeypatch.setattr(render.subprocess, "run", run)
    output, audio = tmp_path / "output.mp4", tmp_path / "audio.mp3"
    render.render_video(scenario, audio_path=audio, scene_words=(
        (Word(.1, .8, "멘트"),), (Word(1.4, 1.9, "마무리"),)), output_path=output,
        work_dir=tmp_path, font_path=cjk_font, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe",
        max_duration=180, background_paths=selected)
    # 9bce983의 정지 경로 명령 전체. 옵션 추가/순서 변경도 회귀다.
    expected = [
        "ffmpeg", "-y", "-threads", "1", "-f", "concat", "-safe", "0", "-i", str(tmp_path / "frames.txt"),
        "-i", str(audio), "-filter_threads", "1", "-vf", ",".join((
            "fps=30", render._drift_filter(), render._subtitle_filter(tmp_path / "phrases.srt", cjk_font.stem),
            "tpad=stop_mode=clone:stop_duration=1")), "-map", "0:v", "-map", "1:a", "-r", "30",
        "-c:v", "libx264", "-threads", "2", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-af", "apad=pad_dur=0.6", "-t", "2.600",
        "-movflags", "+faststart", str(output),
    ]
    assert commands == [expected]


@requires_cjk_font
def test_two_stage_commands_offsets_and_timeline_match_still(tmp_path, monkeypatch, cjk_font):
    scenario = _scenario()
    scenario = replace(scenario, scenes=(replace(scenario.scenes[0], options=(("질문", "50%", .5),)),
                                         scenario.scenes[1]))
    words = ((Word(.1, 1, "멘트"),), (Word(6.5, 7, "마무리"),))
    monkeypatch.setattr(render, "probe_duration", lambda *a, **k: 10)
    monkeypatch.setattr(render, "render_frame", lambda scene, path, **k: path.touch())
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return _fake_run(command, **kwargs)
    monkeypatch.setattr(render.subprocess, "run", run)
    kwargs = dict(audio_path=tmp_path / "voice.mp3", scene_words=words, work_dir=tmp_path,
                  font_path=cjk_font, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", max_duration=180)
    output = tmp_path / "clip.mp4"
    render.render_video(scenario, **kwargs, output_path=output, background_paths=(tmp_path / "input.mp4", None))
    clip_commands = [cmd for cmd in commands if "-stream_loop" in cmd]
    offsets = [float(cmd[cmd.index("-ss") + 1]) for cmd in clip_commands]
    assert len(offsets) > 2 and offsets[0] == 0 and offsets == sorted(set(offsets))
    assert all(cmd.count("-i") == 2 and "-frames:v" in cmd for cmd in clip_commands)
    assert all("fps=" not in cmd[cmd.index("-filter_complex") + 1] for cmd in clip_commands)
    still_commands = [cmd for cmd in commands[:-1] if "-stream_loop" not in cmd]
    assert all(cmd.count("-i") == 1 and "crop=" in cmd[cmd.index("-vf") + 1] for cmd in still_commands)
    final_filter = commands[-1][commands[-1].index("-vf") + 1]
    assert "subtitles=" in final_filter and "tpad=" in final_filter and "crop=" not in final_filter
    assert "reverse" not in str(commands) and "xfade" not in str(commands)
    render.render_video(scenario, **kwargs, output_path=tmp_path / "still.mp4")
    assert json.loads(output.with_suffix(".timeline.json").read_text(encoding="utf-8")) == json.loads(
        (tmp_path / "still.timeline.json").read_text(encoding="utf-8"))


@pytest.fixture
def media_binaries():
    settings = Settings.from_env()
    if not all(shutil.which(binary) for binary in (settings.ffmpeg_bin, settings.ffprobe_bin)):
        pytest.skip("FFmpeg와 ffprobe가 필요합니다")
    return settings


@pytest.fixture
def testsrc(tmp_path, media_binaries):
    path = tmp_path / "testsrc.mp4"
    subprocess.run([media_binaries.ffmpeg_bin, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=720x1280:rate=30", "-t", "2", "-c:v", "libx264", "-threads", "2",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)
    return path


def test_real_validation_rejects_short_and_broken_accepts_four_seconds(testsrc, media_binaries):
    with pytest.raises(ValueError, match="4 seconds"):
        clips.validate_clip(testsrc, media_binaries, time.monotonic() + 30)
    valid = testsrc.with_name("valid.mp4")
    subprocess.run([media_binaries.ffmpeg_bin, "-y", "-v", "error", "-stream_loop", "1", "-i", str(testsrc),
                    "-t", "4", "-c", "copy", str(valid)], check=True, capture_output=True)
    clips.validate_clip(valid, media_binaries, time.monotonic() + 30)
    valid.write_bytes(b"broken")
    with pytest.raises(subprocess.CalledProcessError):
        clips.validate_clip(valid, media_binaries, time.monotonic() + 30)


@requires_cjk_font
def test_real_mixed_clip_still_render_duration_resolution_and_audio(testsrc, media_binaries, tmp_path, cjk_font):
    audio, output, png = tmp_path / "voice.wav", tmp_path / "mixed.mp4", tmp_path / "still.png"
    Image.new("RGB", (1080, 1920), "#304050").save(png)
    subprocess.run([media_binaries.ffmpeg_bin, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=24000", "-t", "6", str(audio)], check=True, capture_output=True)
    scenario = _scenario()
    scenario = replace(scenario, scenes=(replace(scenario.scenes[0], options=(("질문", "50%", .5),)),
                                         scenario.scenes[1]))
    duration = render.render_video(scenario, audio_path=audio, scene_words=(
        (Word(.1, 1, "멘트"),), (Word(5.2, 5.8, "마무리"),)), output_path=output, work_dir=tmp_path,
        font_path=cjk_font, ffmpeg_bin=media_binaries.ffmpeg_bin, ffprobe_bin=media_binaries.ffprobe_bin,
        max_duration=180, background_paths=(testsrc, png))
    result = subprocess.run([media_binaries.ffprobe_bin, "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(output)], check=True, capture_output=True, text=True)
    info = json.loads(result.stdout)
    video = next(row for row in info["streams"] if row["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (1080, 1920, "30/1")
    assert any(row["codec_type"] == "audio" for row in info["streams"])
    assert float(info["format"]["duration"]) == pytest.approx(duration, abs=.1)
    assert duration == pytest.approx(6.6)
    timeline = json.loads(output.with_suffix(".timeline.json").read_text(encoding="utf-8"))
    assert sum(row["duration"] for row in timeline) == pytest.approx(duration, abs=.01)


@pytest.mark.parametrize("metadata", [
    {"streams": [{"width": 480, "height": 854, "codec_name": "h264"}], "format": {"duration": "8"}},
    {"streams": [{"width": 720, "height": 1280, "codec_name": "h264"}], "format": {"duration": "nan"}},
    {"streams": [{"width": 720, "height": 1280, "codec_name": ""}], "format": {"duration": "8"}},
])
def test_validation_rejects_dimensions_duration_and_codec(metadata, monkeypatch, tmp_path):
    monkeypatch.setattr(clips.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=json.dumps(metadata)))
    with pytest.raises(ValueError):
        clips.validate_clip(tmp_path / "bad.mp4", Settings.from_env(), time.monotonic() + 30)
