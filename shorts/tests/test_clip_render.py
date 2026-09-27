from dataclasses import replace
from array import array
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import math
from types import SimpleNamespace

from PIL import Image
import pytest

from polymarket_shorts import blender_render, clips, render
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
    manifest = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
    Path(manifest["output"]).touch()
    return SimpleNamespace(returncode=0, stdout="", stderr="")


@requires_cjk_font
@pytest.mark.parametrize("mode", ["disabled", "no_key", "no_clips"])
def test_still_fallback_uses_blender(tmp_path, monkeypatch, cjk_font, mode):
    scenario = _scenario()
    settings = replace(Settings.from_env(), generated_clips=mode != "disabled",
                       video_api_key="" if mode == "no_key" else "test-key", visuals_enabled=True)
    png = tmp_path / (hashlib.sha1(scenario.scenes[0].visual_query.encode()).hexdigest()[:12] + ".png")
    png.touch()
    monkeypatch.setattr(clips, "_generate_clip", lambda *args: None)
    selected = clips.clips_for(scenario.scenes, (png, None), settings)
    assert selected == (png, None)
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
        work_dir=tmp_path, font_path=cjk_font, blender_bin="blender", ffprobe_bin="ffprobe",
        max_duration=180, background_paths=selected)
    assert len(commands) == 1 and commands[0][0] == "blender"
    manifest = json.loads((tmp_path / "blender-manifest.json").read_text(encoding="utf-8"))
    assert manifest["movies"] == []
    assert all(row["drift"] for row in manifest["images"])


@requires_cjk_font
def test_movie_scene_and_timeline_match_still(tmp_path, monkeypatch, cjk_font):
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
                  font_path=cjk_font, blender_bin="blender", ffprobe_bin="ffprobe", max_duration=180)
    output = tmp_path / "clip.mp4"
    render.render_video(scenario, **kwargs, output_path=output, background_paths=(tmp_path / "input.mp4", None))
    assert len(commands) == 1
    manifest = json.loads((tmp_path / "blender-manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["movies"]) == 1
    assert manifest["movies"][0]["start"] == 0
    assert manifest["movies"][0]["duration"] == 5.95
    assert any(row["drift"] for row in manifest["images"])
    assert any(not row["drift"] for row in manifest["images"])
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
@pytest.mark.parametrize("clip_fps", [24, 30])
def test_real_mixed_clip_still_render_duration_resolution_and_audio(
    testsrc, media_binaries, tmp_path, cjk_font, clip_fps,
):
    if not shutil.which(media_binaries.blender_bin):
        pytest.skip("Blender is required")
    audio, output, png = tmp_path / "voice.wav", tmp_path / "mixed.mp4", tmp_path / "still.png"
    loop = tmp_path / "loop.mp4"
    subprocess.run([media_binaries.ffmpeg_bin, "-y", "-v", "error", "-i", str(testsrc),
                    "-t", "0.5", "-r", str(clip_fps), "-c:v", "libx264", "-threads", "2", str(loop)],
                   check=True, capture_output=True)
    Image.new("RGB", (1080, 1920), "#304050").save(png)
    subprocess.run([media_binaries.ffmpeg_bin, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=24000", "-t", "2.4", str(audio)], check=True, capture_output=True)
    scenario = _scenario()
    scenario = replace(scenario, scenes=(replace(scenario.scenes[0], options=(("질문", "50%", .5),)),
                                         scenario.scenes[1]))
    duration = render.render_video(scenario, audio_path=audio, scene_words=(
        (Word(.1, 1, "멘트"),), (Word(1.8, 2.3, "마무리"),)), output_path=output, work_dir=tmp_path,
        font_path=cjk_font, blender_bin=media_binaries.blender_bin, ffprobe_bin=media_binaries.ffprobe_bin,
        max_duration=180, background_paths=(loop, png))
    result = subprocess.run([media_binaries.ffprobe_bin, "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(output)], check=True, capture_output=True, text=True)
    info = json.loads(result.stdout)
    video = next(row for row in info["streams"] if row["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (1080, 1920, "30/1")
    assert any(row["codec_name"] == "aac" for row in info["streams"])
    assert video["pix_fmt"] == "yuv420p"
    assert float(info["format"]["duration"]) == pytest.approx(duration, abs=1 / 30)
    assert duration == pytest.approx(3.0)
    timeline = json.loads(output.with_suffix(".timeline.json").read_text(encoding="utf-8"))
    assert sum(row["duration"] for row in timeline) == pytest.approx(duration, abs=.01)
    expected = render._scene_durations(render._phrases(
        [s.narration for s in scenario.scenes],
        ((Word(.1, 1, "멘트"),), (Word(1.8, 2.3, "마무리"),)), duration), duration)
    assert timeline[-1]["start"] == expected[0]
    # AAC 재인코딩 후에도 발화 샘플의 시각과 내용이 유지된다.
    samples = []
    for path in (audio, output):
        decoded = subprocess.run([media_binaries.ffmpeg_bin, "-v", "error", "-i", str(path),
                                  "-vn", "-ac", "1", "-ar", "24000", "-f", "s16le", "-"],
                                 check=True, capture_output=True).stdout
        pcm = array("h")
        pcm.frombytes(decoded)
        samples.append(pcm)
    pairs = list(zip(*samples))
    correlation = sum(a * b for a, b in pairs) / math.sqrt(
        sum(a * a for a, _ in pairs) * sum(b * b for _, b in pairs))
    assert correlation > .99
    assert max(abs(value) for value in samples[1][int(2.5 * 24000):]) < 5
    # 반복 경계 뒤에도 배경이 나오고, 투명 카드와 자막이 올바르게 합성된다.
    raw = subprocess.run([media_binaries.ffmpeg_bin, "-v", "error", "-ss", "0.8", "-i", str(output),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    frame = Image.frombytes("RGB", (1080, 1920), raw)
    assert frame.crop((0, 0, 1080, 150)).getextrema()[0][1] > 30
    caption = frame.crop((0, 1400, 1080, 1600))
    lit = [(x, y + 1400) for y in range(caption.height) for x in range(caption.width)
           if min(caption.getpixel((x, y))) > 180]
    assert lit and min(x for x, _ in lit) >= 72 and max(x for x, _ in lit) <= 890
    assert max(y for _, y in lit) <= 1540


@pytest.mark.parametrize("metadata", [
    {"streams": [{"width": 480, "height": 854, "codec_name": "h264"}], "format": {"duration": "8"}},
    {"streams": [{"width": 720, "height": 1280, "codec_name": "h264"}], "format": {"duration": "nan"}},
    {"streams": [{"width": 720, "height": 1280, "codec_name": ""}], "format": {"duration": "8"}},
])
def test_validation_rejects_dimensions_duration_and_codec(metadata, monkeypatch, tmp_path):
    monkeypatch.setattr(clips.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=json.dumps(metadata)))
    with pytest.raises(ValueError):
        clips.validate_clip(tmp_path / "bad.mp4", Settings.from_env(), time.monotonic() + 30)


def test_missing_blender_is_a_clear_render_error(tmp_path, cjk_font):
    with pytest.raises(render.RenderError, match="BLENDER_BIN"):
        blender_render.compose(images=[], movies=[], subtitles=[], audio_path=tmp_path / "a.wav",
                               output_path=tmp_path / "out.mp4", work_dir=tmp_path,
                               font_path=cjk_font, duration=3,
                               blender_bin="definitely-not-blender")


def test_blender_explicit_missing_path_is_not_replaced(monkeypatch, tmp_path):
    missing = str(tmp_path / "missing-blender.exe")
    monkeypatch.setenv("BLENDER_BIN", missing)
    assert Settings.from_env().blender_bin == missing
