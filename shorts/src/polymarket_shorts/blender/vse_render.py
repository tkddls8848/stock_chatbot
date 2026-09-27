"""Blender 5.2의 Python으로 실행하는 헤드리스 VSE 합성기 (외부 패키지 불필요)."""
import json
import math
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

    for index, row in enumerate(manifest["movies"]):
        start, end = frame(row["start"]), frame(row["start"] + row["duration"])
        cursor = start
        while cursor < end:
            movie = strips.new_movie(f"movie-{index}-{cursor}", row["path"], channel=1,
                                     frame_start=cursor, fit_method="STRETCH")
            length = movie.frame_final_duration
            if movie.fps != fps:
                # 입력이 24/25fps여도 재생 속도를 바꾸지 않는다.
                last = movie.retiming_keys.add(timeline_frame=cursor + length)
                length = max(1, round(length * fps / movie.fps))
                last.timeline_frame = cursor + length
            movie.frame_final_end = min(cursor + length, end)
            # 색 곱과 밝기 오프셋은 이전 배경 필터와 같은 sRGB 공간에서 적용한다.
            modifier = movie.modifiers.new("tone", "CURVES")
            mapping = modifier.curve_mapping
            for curve, multiply in zip(mapping.curves[:3], row["multiply"]):
                curve.points[0].location = (0, row["brightness"])
                curve.points[-1].location = (1, multiply + row["brightness"])
            mapping.update()
            cursor += length

    for index, row in enumerate(manifest["images"]):
        start, end = frame(row["start"]), frame(row["start"] + row["duration"])
        if end <= start:
            continue
        strip = strips.new_image(f"card-{index}", row["path"], channel=2, frame_start=start)
        strip.frame_final_end = end
        strip.blend_type = "ALPHA_OVER"
        if index == len(manifest["images"]) - 1:
            strip.frame_final_end += round(manifest["clone_padding_seconds"] * fps)

    caption = manifest["caption"]
    font = bpy.data.fonts.load(caption["font"])
    for index, row in enumerate(manifest["subtitles"]):
        start, end = frame(row["start"]), frame(row["end"])
        if end <= start:
            continue
        text = strips.new_effect(f"caption-{index}", type="TEXT", channel=3,
                                 frame_start=start, length=end - start)
        text.text, text.font, text.font_size = row["text"], font, caption["em_size"]
        text.anchor_y = "BOTTOM"
        text.use_bold = caption["synthetic_bold"]
        text.color = (245 / 255, 241 / 255, 232 / 255, 1)
        text.location = ((caption["left"] + width - caption["right"]) / (2 * width),
                         caption["bottom"] / height)
        text.wrap_width = 0
        text.use_outline = True
        text.outline_color = (17 / 255, 13 / 255, 8 / 255, 1)
        text.outline_width = 5 / caption["em_size"]
        text.use_shadow = True
        text.shadow_color = (0, 0, 0, .41)
        text.shadow_offset = 2 / caption["em_size"]
        text.shadow_angle = math.radians(135)
    strips.new_sound("narration", manifest["audio"], channel=4, frame_start=1)
    bpy.ops.render.render(animation=True)


if __name__ == "__main__":
    render(json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8")))
