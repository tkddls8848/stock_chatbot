"""Blender 5.2의 Python으로 실행하는 헤드리스 VSE 합성기 (외부 패키지 불필요)."""
import json
from pathlib import Path
import sys

import bpy


def render(manifest):
    scene = bpy.context.scene
    width, height, fps = (manifest[key] for key in ("width", "height", "fps"))
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    scene.frame_start, scene.frame_end = 1, round(manifest["duration"] * fps)
    scene.render.use_sequencer = True
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 2
    scene.render.image_settings.media_type = "VIDEO"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.ffmpeg.ffmpeg_preset = "GOOD"
    scene.render.ffmpeg.audio_codec = "AAC"
    scene.render.ffmpeg.audio_bitrate = 192
    scene.render.ffmpeg.audio_mixrate = 48000
    scene.render.ffmpeg.audio_channels = "MONO"
    scene.render.filepath = manifest["output"]
    # PNG의 sRGB 값을 보존한다. AgX는 카드 색까지 바꾼다.
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.sequencer_colorspace_settings.name = "sRGB"
    editor = scene.sequence_editor_create()
    editor.use_cache_raw = False
    editor.use_cache_final = False
    editor.use_prefetch = False
    bpy.context.preferences.system.memory_cache_limit = 32
    strips = editor.strips

    def frame(seconds):
        return 1 + round(seconds * fps)

    for index, row in enumerate(manifest["images"]):
        start, end = frame(row["start"]), frame(row["start"] + row["duration"])
        if end <= start:
            continue
        strip = strips.new_image(f"card-{index}", row["path"], channel=2, frame_start=start)
        strip.frame_final_end = end
        strip.blend_type = "ALPHA_OVER"
        if index == len(manifest["images"]) - 1:
            strip.frame_final_end += round(manifest["clone_padding_seconds"] * fps)

    # 자막은 렌더가 그린 화면 크기 투명 PNG다(상자·글자 위치가 그림 안에 있다).
    for index, row in enumerate(manifest["subtitles"]):
        start, end = frame(row["start"]), frame(row["end"])
        if end <= start:
            continue
        strip = strips.new_image(f"caption-{index}", row["path"], channel=3, frame_start=start)
        strip.frame_final_end = end
        strip.blend_type = "ALPHA_OVER"
    narration = strips.new_sound("narration", manifest["audio"], channel=4, frame_start=1)
    narration.volume = manifest.get("narration_gain", 1.0)
    bpy.ops.render.render(animation=True)


if __name__ == "__main__":
    render(json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8")))
