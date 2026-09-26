"""클립이 있는 영상만 쓰는 두 단계 인코더. 정지 전용 렌더 명령은 건드리지 않는다."""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Sequence

from . import render
from .review import write_json
from .scenario import Scenario
from .tts import Word


def _encode(command: list[str], target: Path) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode or not target.is_file():
        raise render.RenderError(f"ffmpeg 클립 렌더링 실패: {(result.stderr or result.stdout)[-1000:]}")


def _clip_filter(accent: str, index: int) -> str:
    tone = render._COLORS.get(accent, render._COLORS["gold"]).lstrip("#")
    channels = [1 - render._TONE_STRENGTH + render._TONE_STRENGTH * int(tone[n:n + 2], 16) / 255
                for n in (0, 2, 4)]
    brightness = render._BRIGHTNESS[index % len(render._BRIGHTNESS)]
    return (f"scale={render.WIDTH}:{render.HEIGHT}:flags=lanczos,setsar=1,"
            f"colorchannelmixer=rr={channels[0]:.5f}:gg={channels[1]:.5f}:bb={channels[2]:.5f},"
            f"eq=brightness={(brightness - 1) * .2:.5f}")


def render_video(
    scenario: Scenario, *, audio_path: Path, scene_words: Sequence[Sequence[Word]],
    output_path: Path, work_dir: Path, font_path: Path, ffmpeg_bin: str,
    ffprobe_bin: str, max_duration: float,
    background_paths: tuple[Path | None, ...],
) -> float:
    duration = render.probe_duration(audio_path, ffprobe_bin=ffprobe_bin) + .6
    phrases = render._phrases([scene.narration for scene in scenario.scenes], scene_words, duration)
    durations = render._scene_durations(phrases, duration)
    captions = work_dir / "phrases.srt"
    render._write_captions(phrases, captions, font_path=font_path)
    if len(background_paths) != len(scenario.scenes):
        raise render.RenderError("배경 수와 장면 수가 다릅니다")
    parts, timeline = [], []
    cursor, merging, encoded_frames = 0.0, None, 0
    for index, (scene, seconds, background) in enumerate(
        zip(scenario.scenes, durations, background_paths, strict=True), start=1,
    ):
        offset = 0.0
        is_clip = background is not None and background.suffix == ".mp4"
        for position, (beat, hold, display, shown) in enumerate(render._beats(scene, seconds), start=1):
            stem = f"clip-{index:02d}-{position:02d}-{beat}"
            frame, part = work_dir / f"{stem}.png", work_dir / f"{stem}.mp4"
            render.render_frame(display, frame, font_path=font_path, index=index,
                                total=len(scenario.scenes), background_path=background,
                                shown=shown, transparent=is_clip)
            command = [ffmpeg_bin, "-y", "-threads", "1"]
            if is_clip:
                # 영상 하나 + PNG 하나. loop 영상의 장면 내 위치를 이어 받아 비트마다
                # 카메라가 처음으로 튀지 않는다. 이미지 두 스트림의 fps/overlay는 금지다.
                command += ["-stream_loop", "-1", "-ss", f"{offset:.6f}", "-i", str(background),
                            "-loop", "1", "-framerate", "30", "-i", str(frame),
                            "-filter_complex_threads", "1", "-filter_complex",
                            f"[0:v]{_clip_filter(scene.accent, index)}[bg];"
                            "[bg][1:v]overlay=shortest=1:format=auto,format=yuv420p[v]",
                            "-map", "[v]"]
            else:
                # 정지 장면은 Pillow 합성 PNG 한 장만 입력한다. 전체 영상의 시각으로
                # drift를 계산해 비트 경계에서 좌표가 다시 시작되지 않게 한다.
                command += ["-loop", "1", "-framerate", "30", "-i", str(frame),
                            "-filter_threads", "1", "-vf",
                            render._drift_filter().replace("*t/", f"*(t+{cursor:.6f})/") + ",setsar=1"]
            # 각 비트를 독립 반올림하면 카운트업 수십 개에서 오차가 누적된다.
            # 누적 끝점을 30fps 격자로 옮겨 전체 타임라인 오차를 한 프레임 안에 둔다.
            frames = max(1, round((cursor + hold) * 30) - encoded_frames)
            encoded_frames += frames
            command += ["-an", "-r", "30", "-frames:v", str(frames), "-c:v", "libx264",
                        "-threads", "2", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-video_track_timescale", "15360", str(part)]
            _encode(command, part)
            parts.append(part)
            if merging == (index, beat):
                timeline[-1]["duration"] = round(timeline[-1]["duration"] + hold, 3)
            else:
                timeline.append({"start": round(cursor, 3), "duration": round(hold, 3),
                                 "scene": scene.title, "beat": beat})
            merging = (index, beat)
            cursor += hold
            offset += hold
    concat = work_dir / "clips.txt"
    lines = ["file '" + path.resolve().as_posix().replace("'", "'\\''") + "'" for path in parts]
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    filters = ",".join((render._subtitle_filter(captions, font_path.stem),
                        "tpad=stop_mode=clone:stop_duration=1"))
    _encode([
        ffmpeg_bin, "-y", "-threads", "1", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(audio_path), "-filter_threads", "1", "-vf", filters, "-map", "0:v", "-map", "1:a",
        "-r", "30", "-c:v", "libx264", "-threads", "2", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-af", "apad=pad_dur=0.6",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output_path),
    ], output_path)
    write_json(output_path.with_suffix(".timeline.json"), timeline)
    return duration
