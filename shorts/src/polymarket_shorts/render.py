from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
import subprocess
from typing import Iterable, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont, ImageOps

from .scenario import Scenario, Scene
from .tts import Word, locate


WIDTH, HEIGHT = 1080, 1920
_COLORS = {
    "ink": "#F5F1E8",
    "muted": "#BDB6A8",
    "panel": "#24231F",
    "line": "#444039",
    "track": "#2B3A41",
    "gold": "#D4A84F",
    "red": "#E0645C",
    "blue": "#5E8FC9",
}


class RenderError(RuntimeError):
    pass


def find_font(configured: Path | None = None) -> Path:
    candidates = [
        configured,
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("C:/Windows/Fonts/malgunbd.ttf"),
        Path("C:/Windows/Fonts/malgun.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise RenderError("한글 폰트가 없습니다. SHORTS_FONT_FILE을 지정하세요")


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


# ── YouTube Shorts 안전 영역과 세로 배치 ─────────────────
# 세로 1080x1920에서 플레이어 UI가 프레임을 덮는다. 위 ~180px은 검색·내비게이션,
# 아래 ~350px은 채널명·제목·음원 바, 오른쪽 ~192px은 좋아요·댓글·공유 버튼 줄이다.
# 그 밖에 그린 것은 실제 재생 화면에서 보이지 않는다.
#
# 안전 영역을 지키느라 프레임의 아래 절반을 비워 두면 안 된다. 2026-09-23
# 산출물이 정확히 그랬다 — 그림은 위 850px까지만 있고 그 아래는 #101B20 한 색,
# 정보는 위쪽 40%에 몰리고 자막만 중간 높이에 작게 떠 있었다. 배경 사진은 이제
# 프레임 전체를 덮고(`_background`), 본문은 SAFE_BOTTOM 바로 위까지 내려오며,
# 자막은 쇼츠 관례대로 하단 1/3을 크게 차지한다. 안전 영역 아래에는 아무 글자도
# 두지 않지만 사진은 계속 흐르므로 빈 띠가 생기지 않는다.
#
# 세로 순서: 헤더 → 제목 → 진행바 → 본문(선택지·문구) → 잔글씨 → 자막.
SAFE_TOP = 190                          # 이 위는 검색·내비게이션 구역
SAFE_BOTTOM = HEIGHT - 380              # 1540. 이 아래는 Shorts UI 구역
SAFE_LEFT = 72
SAFE_RIGHT = WIDTH - 72                 # 1008. 상단은 오른쪽 버튼 줄이 닿지 않는다
BODY_RIGHT = WIDTH - 202                # 878. 본문은 버튼 줄을 피해 좁게 쓴다
TITLE_TOP, TITLE_BOTTOM = 318, 616
PROGRESS_Y = 677                        # 헤더 밑줄 자리. 예전에는 1580이었다
BODY_TOP = 744
BODY_BOTTOM = 1160
META_Y = 1198                           # 참여 규모·종료 예정 같은 잔글씨
# 선택지 한 줄 안에서 게이지가 앉는 높이(줄 높이의 비율)와 그 두께. 줄의 나머지
# 20%는 다음 줄과의 간격이다 — 막대와 다음 줄 이름이 붙으면 둘이 한 덩어리로 보인다.
OPTION_BAR_TOP = .80
OPTION_BAR = 16
FOOTER_Y = 1288                         # 고지문과 자료 기준. 자막 바로 위다
CAPTION_MARGIN_V = HEIGHT - SAFE_BOTTOM  # 380. 자막 아래 끝을 안전 영역 바닥에 붙인다

# ── 배경 사진 위에 얹는 어둠 ─────────────────────────────
# (y, 불투명도)이고 사이는 선형이다. 제목이 앉는 위쪽은 옅게 두어 사진이 보이고,
# 본문과 자막이 앉는 아래쪽은 짙게 깔아 글자가 읽힌다. 맨 아래는 다시 옅어져
# Shorts UI 구역에서도 사진이 이어진다.
_SCRIM = ((0, .62), (210, .42), (600, .48), (800, .76), (1480, .78), (1740, .66), (HEIGHT, .58))
_SCRIM_RGB = (10, 19, 25)

# 장면마다 같은 사진을 다르게 본다. 확대 여유 안에서 크롭 위치를 옮기고, 짝수
# 장면은 좌우를 뒤집고, 장면의 accent 색으로 색조를 입힌다 — 새 미디어 소스도
# 네트워크도 쓰지 않고 장면 성격에 따라 배경이 달라진다.
_ZOOM = 1.16
_TONE_STRENGTH = .5
_BRIGHTNESS = (1.12, .95, 1.05, 1.0)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """어절 경계에서 줄을 바꿔 '0회', '97.5%' 같은 수치를 한 덩어리로 남긴다."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}" if current else word
            if draw.textlength(candidate, font=font) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = ""
            # 공백 없는 긴 원고도 누락하지 않는다. 한 어절 자체가 폭을 넘을 때만 나눈다.
            for char in word:
                if current and draw.textlength(current + char, font=font) > width:
                    lines.append(current)
                    current = ""
                current += char
        if current:
            lines.append(current)
    return lines


def _background(path: Path | None, *, index: int, total: int, accent: str) -> Image.Image:
    """저장된 배경 한 장을 장면마다 다르게 보이도록 잡는다.

    쓸 수 있는 그림이 둘뿐이라 2026-09-23 산출물은 네 장면이 모두 같은 도시
    야경이었다. 장면별 `visual_query`가 고르는 파일은 그대로 두고, 여기서
    **크롭 위치·좌우 방향·색조·밝기**를 장면 번호와 accent로 정한다. 새 자산도,
    생성 호출도, 네트워크도 없이 장면이 바뀐 것이 눈에 보인다.
    """
    if path is None or not path.is_file():
        return Image.new("RGB", (WIDTH, HEIGHT), "#101B20")
    with Image.open(path) as source:
        wide = ImageOps.fit(source.convert("RGB"), (round(WIDTH * _ZOOM), round(HEIGHT * _ZOOM)),
                            method=Image.Resampling.LANCZOS)
    # 확대 여유를 장면 순서대로 가로는 왼쪽→오른쪽, 세로는 아래→위로 훑는다.
    share = (index - 1) / (total - 1) if total > 1 else .5
    left = round((wide.width - WIDTH) * share)
    top = round((wide.height - HEIGHT) * (1 - share))
    frame = wide.crop((left, top, left + WIDTH, top + HEIGHT))
    if index % 2 == 0:
        frame = ImageOps.mirror(frame)
    tone = Image.new("RGB", frame.size, _COLORS.get(accent, _COLORS["gold"]))
    frame = Image.blend(frame, ImageChops.multiply(frame, tone), _TONE_STRENGTH)
    return ImageEnhance.Brightness(frame).enhance(_BRIGHTNESS[index % len(_BRIGHTNESS)])


def _scrim(draw: ImageDraw.ImageDraw) -> None:
    """배경 사진 위에 세로 그라데이션을 깔아 글자가 읽히게 한다."""
    for (top, start), (bottom, stop) in zip(_SCRIM, _SCRIM[1:]):
        for y in range(top, min(bottom, HEIGHT)):
            share = (y - top) / max(1, bottom - top)
            draw.line((0, y, WIDTH, y), fill=(*_SCRIM_RGB, round(255 * (start + (stop - start) * share))))


def _text_block(draw, text, font_path, box, *, size=48, color="#F5F1E8", center=False):
    """Fit all text inside a bounded box; never silently discard lines.

    `center`면 남는 세로 여백을 위아래로 나눈다. 한 줄짜리 제목을 위에 붙여 두면
    제목과 진행바 사이에 200px짜리 구멍이 생긴다 — 칸의 크기는 가장 긴 문구에
    맞춰 잡아야 하고, 짧은 문구는 그 안에서 가운데 선다.
    """
    x, y, right, bottom = box
    if not text.strip():
        return
    for candidate in range(size, 25, -2):
        font = _font(font_path, candidate)
        lines = _wrap(draw, text, font, right - x)
        spacing = round(candidate * 1.4)
        if len(lines) * spacing <= bottom - y:
            if center:
                y += (bottom - y - len(lines) * spacing) // 2
            for line in lines:
                draw.text((x, y), line, font=font, fill=color)
                y += spacing
            return
    raise RenderError("화면 텍스트가 안전 영역을 넘습니다: " + text[:70])


def _options_block(draw, scene: Scene, font_path: Path, accent: str, box, *, shown: int) -> None:
    """선택지를 이름 + 큰 '예' 확률 + 게이지 한 줄로 그린다.

    예전 화면은 원자료 형식을 그대로 옮겨 "9월 WTI 90달러 이하: 예 99.95%,
    아니오 0.05%" 한 덩어리였다. 어색한 자리에서 줄이 바뀌고, 어느 숫자를
    봐야 하는지도 알 수 없었다. 이지선다에서 아니오는 예의 나머지이므로 화면은
    '예' 확률 하나만 크게 세우고 막대로 그 크기를 보여 준다.

    `shown`은 지금까지 등장한 줄 수다. 빈 게이지 홈은 처음부터 전부 그려 두어,
    줄이 하나씩 차오르는 동안에도 자리가 잡혀 있고 이미 뜬 줄이 밀리지 않는다.
    """
    x, top, right, bottom = box
    height = (bottom - top) / max(1, len(scene.options))
    for position, (label, percent, probability) in enumerate(scene.options):
        y = top + position * height
        bar_y = y + round(height * OPTION_BAR_TOP)
        draw.rounded_rectangle((x, bar_y, right, bar_y + OPTION_BAR), radius=8, fill=_COLORS["track"])
        if position >= shown:
            continue
        _text_block(draw, label, font_path, (x, y, right, y + round(height * .29)),
                    size=40, color=_COLORS["ink"])
        number = _font(font_path, round(height * .38))
        number_y = y + round(height * .30)
        draw.text((x, number_y), percent, font=number, fill=accent)
        draw.text((x + draw.textlength(percent, font=number) + 18, number_y + round(height * .26)),
                  "예", font=_font(font_path, 34), fill=_COLORS["muted"])
        filled = round((right - x) * min(1.0, max(0.0, probability)))
        if filled > OPTION_BAR:
            draw.rounded_rectangle((x, bar_y, x + filled, bar_y + OPTION_BAR), radius=8, fill=accent)


def render_frame(
    scene: Scene,
    path: Path,
    *,
    font_path: Path,
    index: int,
    total: int,
    background_path: Path | None = None,
    shown: int | None = None,
    transparent: bool = False,
) -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    accent = _COLORS.get(scene.accent, _COLORS["gold"])
    _scrim(draw)
    draw.rounded_rectangle((SAFE_LEFT, SAFE_TOP, SAFE_LEFT + 123, SAFE_TOP + 36), radius=7, fill=accent)
    draw.text((SAFE_LEFT + 15, SAFE_TOP + 3), "NUNCHI", font=_font(font_path, 22), fill="#101B20")
    draw.text((216, SAFE_TOP + 3), "CONSENSUS NOTES", font=_font(font_path, 23), fill=_COLORS["ink"])
    draw.text((SAFE_LEFT, 258), scene.kicker, font=_font(font_path, 26), fill=accent)
    _text_block(draw, scene.title, font_path, (SAFE_LEFT, TITLE_TOP, SAFE_RIGHT, TITLE_BOTTOM),
                size=86, center=True)
    draw.text((SAFE_LEFT, 644), f"{index:02d} / {total:02d}", font=_font(font_path, 26), fill=_COLORS["ink"])
    # 진행 상태바. 화면 아래 끝은 자막 자리라 페이지 번호 옆으로 올렸다.
    span = SAFE_RIGHT - 210
    for slot in range(total):
        left = 210 + slot * (span / total)
        draw.rectangle((left, PROGRESS_Y, left + span / total - 8, PROGRESS_Y + 5),
                       fill=accent if slot < index else "#354348")

    if scene.options:
        _options_block(draw, scene, font_path, accent, (SAFE_LEFT, BODY_TOP, BODY_RIGHT, BODY_BOTTOM),
                       shown=len(scene.options) if shown is None else shown)
    else:
        _text_block(draw, scene.body, font_path, (SAFE_LEFT, BODY_TOP, BODY_RIGHT, BODY_BOTTOM),
                    size=66, center=True)

    # 근거 잔글씨. 이벤트 참여 규모·종료 예정·표시한 선택지 수를 그대로 적는다.
    _text_block(draw, " · ".join(b.replace(" · ", " ") for b in scene.bullets),
                font_path, (SAFE_LEFT, META_Y, BODY_RIGHT, META_Y + 76), size=27, color=_COLORS["muted"])
    draw.text((SAFE_LEFT, FOOTER_Y),
              "막대는 '예' 쪽 확률 · 집단 예측 컨센서스 · 투자 조언 아님" if scene.options
              else "집단 예측 컨센서스 · 투자 조언 아님",
              font=_font(font_path, 23), fill=_COLORS["muted"])
    draw.text((SAFE_LEFT, FOOTER_Y + 36), scene.source_note + (" · AI 배경" if background_path else ""),
              font=_font(font_path, 22), fill=accent)
    if not transparent:
        background = _background(background_path, index=index, total=total, accent=scene.accent)
        image = Image.alpha_composite(background.convert("RGBA"), image).convert("RGB")
    image.save(path, "PNG")


def probe_duration(audio_path: Path, *, ffprobe_bin: str) -> float:
    command = [
        ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(audio_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RenderError(f"ffprobe 실패: {(result.stderr or result.stdout)[-500:]}")
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError) as exc:
        raise RenderError("오디오 길이를 읽지 못했습니다") from exc


def _concat_file(frames: Iterable[Path], durations: Iterable[float], target: Path) -> None:
    lines: list[str] = []
    frame_list = list(frames)
    for frame, duration in zip(frame_list, durations, strict=True):
        safe = frame.resolve().as_posix().replace("'", "'\\''")
        lines.extend((f"file '{safe}'", f"duration {duration:.3f}"))
    safe_last = frame_list[-1].resolve().as_posix().replace("'", "'\\''")
    lines.append(f"file '{safe_last}'")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _drift_filter() -> str:
    """정지 카드가 숨 쉬게 하는 아주 느린 흐름.

    자르는 창의 크기는 고정이고 위치만 매 프레임 계산된다 — FFmpeg의 crop은
    출력 크기를 한 번만 정하므로 확대는 할 수 없고, 이동만 한다. 되돌려 키우는
    비율은 1.5%라 글자가 무뎌지지 않는다.
    """
    period_x, period_y = DRIFT_PERIODS
    return (
        f"crop=w={WIDTH - 2 * DRIFT_MARGIN}:h={HEIGHT - 2 * DRIFT_MARGIN}"
        f":x='{DRIFT_MARGIN}+{DRIFT_AMPLITUDE}*sin(2*PI*t/{period_x})'"
        f":y='{DRIFT_MARGIN}+{DRIFT_AMPLITUDE}*sin(2*PI*t/{period_y})',"
        f"scale={WIDTH}:{HEIGHT}"
    )


# 자막은 쇼츠 관례대로 하단 1/3을 크게 차지한다. 38은 1080 폭에서 본문보다 작아
# 화면 중간에 떠 있는 주석처럼 보였다. 이 크기면 한 줄에 한글 13자쯤 들어가고,
# 발화 한 덩어리(_PHRASE_CHARS)가 1~2줄로 떨어진다.
CAPTION_FONT_SIZE = 56
CAPTION_MARGIN_L = 72
CAPTION_MARGIN_R = 190                  # 오른쪽 좋아요·댓글 버튼 줄
# 줄바꿈은 우리가 어절 경계에서 넣고 libass는 그대로 그린다(WrapStyle=2). libass는
# 한글도 중국어·일본어처럼 아무 글자에서나 끊어서, "10월 금리 변동 없음과"가
# "…없" / "음과 …"로 갈라졌다. 합성 볼드가 측정보다 넓어질 수 있어 여유를 둔다.
CAPTION_WIDTH = round((WIDTH - CAPTION_MARGIN_L - CAPTION_MARGIN_R) * .92)


def _subtitle_filter(path: Path, font_name: str = "Noto Sans CJK KR") -> str:
    escaped = path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
    style = (
        f"PlayResX={WIDTH},PlayResY={HEIGHT},FontName={font_name},FontSize={CAPTION_FONT_SIZE},"
        "Bold=1,PrimaryColour=&H00F5F1E8,OutlineColour=&H00080D11,BackColour=&H96000000,"
        "BorderStyle=1,Outline=5,Shadow=2,WrapStyle=2,"
        # MarginV는 아래 가장자리로부터의 거리다. 이 값이면 자막의 아래 끝이
        # SAFE_BOTTOM(1540)에 닿는다 — Shorts UI에 가리지 않는 가장 아래다.
        f"Alignment=2,MarginV={CAPTION_MARGIN_V},MarginL={CAPTION_MARGIN_L},MarginR={CAPTION_MARGIN_R}"
    )
    return f"subtitles='{escaped}':force_style='{style}'"


# 자막은 자기 첫 단어보다 이만큼 먼저 뜬다. edge-tts의 문장 큐가 쓰던 값과 같다.
CAPTION_LEAD = 0.05
# 장면이 바뀔 때는 더 일찍 넘긴다. tts가 넓혀 둔 장면 경계 쉼의 뒤쪽 이만큼이
# 새 화면 위에서 흐르므로, 화면이 먼저 자리를 잡은 뒤에 말이 시작된다.
SCENE_LEAD = 0.55
# 한 자막에 담는 글자 수. 커진 자막(CAPTION_FONT_SIZE)에서 두 줄에 들어가는 양이다.
_PHRASE_CHARS = 26
_SENTENCE_END = (".", "?", "!")
# 끊기 좋은 자리와 나쁜 자리. 쉼표는 말하는 사람이 이미 쉬는 자리이고 연결어미
# ("…다르니")도 한 마디가 끝나는 자리다. 반대로 "…와·…과·…의"는 다음 말에 붙는
# 조사라 그 뒤에서 끊으면 "숫자와" / "함께"처럼 한 덩어리가 갈라진다.
_CLAUSE_END = re.compile(r"(?:니|고|며|면|서|는데|지만)$")
_BINDING_END = ("와", "과", "의")

# ── 움직임 ─────────────────────────────────────────────
# 정지 카드를 20초씩 그대로 세워 두면 화면이 멈춘 것처럼 보인다. 움직임은 셋뿐이고
# 전부 기존 렌더 경로 안에서 만든다 — 새 입력도, 새 의존성도, 새 네트워크도 없다.
#
# 1. 수치 카운트업: 큰 숫자가 0에서 제자리까지 차오른다. 정지 PNG 여러 장일 뿐이다.
# 2. 선택지의 순차 등장: 선택지 줄이 말의 순서대로 하나씩 쌓이고, 새로 뜬 줄의
#    숫자가 차오른다. 자리는 처음부터 잡혀 있어 이미 뜬 줄이 밀리지 않는다.
# 3. 프레임 전체의 느린 흐름: 가장자리를 조금 잘라 내고 그 안에서 천천히 움직인다.
COUNTUP_SECONDS = .72
COUNTUP_STEPS = 8
# 카운트업이 끝난 뒤에도 제자리 숫자가 머물 시간이 남아야 한다.
COUNTUP_MIN_BEAT = 1.4
# 잘라 내는 가장자리(px)와 그 안에서 움직이는 폭. 되돌려 키우는 비율이 1.5%라
# 글자가 무뎌지지 않고, 자막은 이 뒤에 얹으므로 흔들리지 않는다.
DRIFT_MARGIN = 8
DRIFT_AMPLITUDE = 7
# 가로·세로 주기(초). 서로 나누어떨어지지 않아 같은 자리로 돌아오지 않는다.
DRIFT_PERIODS = (23, 31)
_METRIC_NUMBER = re.compile(r"(\d+(?:\.\d+)?)(%?)$")


@dataclass(frozen=True)
class Phrase:
    """화면에 한 번에 뜨는 자막 한 덩어리."""

    start: float
    end: float
    text: str


def _phrases(
    narrations: Sequence[str], scenes: Sequence[Sequence[Word]], duration: float,
) -> tuple[tuple[Phrase, ...], ...]:
    """장면 원고를 실제 단어 경계에 맞춰 자막 문구로 나눈다.

    시각은 전부 edge-tts가 보고한 단어 구간에서 온다 — 긴 문장을 구절로 쪼갤 때
    글자 수로 시간을 배분하던 추정이 없다. 그 추정은 문장 뒤 쉼(약 0.86초)까지
    포함한 창을 나눠서 분할점이 늘 뒤로 밀렸고, 뒷 구절이 말보다 0.4~1.0초 늦게
    떴다. 앞 구절은 그만큼 더 남아 다음 구절의 음성과 겹쳤다.

    문구는 자기 첫 단어보다 CAPTION_LEAD(장면의 첫 문구는 SCENE_LEAD)만큼 먼저
    떠서 다음 문구가 뜰 때까지 남는다. 그래서 빈틈도 겹침도 생기지 않는다.
    """
    marked: list[tuple[float, str]] = []
    counts: list[int] = []
    for narration, words in zip(narrations, scenes, strict=True):
        starts = locate(narration, words)
        # 한 단어가 차지하는 원고 구간은 다음 단어 직전까지다. 사이의 문장부호는 앞
        # 단어에 붙어 화면에 그대로 남고, 공백과 줄바꿈만 한 칸으로 줄어든다.
        ends = starts[1:] + [len(narration)]

        def text_of(first: int, last: int, starts=starts, ends=ends, narration=narration) -> str:
            return re.sub(r"\s+", " ", narration[starts[first]:ends[last]]).strip()

        def breakable(at: int, starts=starts, words=words, narration=narration) -> bool:
            """다음 단어와 사이에 공백이 있는가.

            edge-tts는 한 어절도 여러 단어로 돌려준다 — "25bp"가 "25"와 "bp"로
            나뉘어 왔다. 그 사이에서 자막을 끊으면 화면에 "…금리 25"와
            "bp 인상,"이 따로 뜬다.
            """
            between = narration[starts[at] + len(words[at].text):starts[at + 1]]
            return bool(re.search(r"\s", between))

        # 먼저 문장으로 끊는다. 한 문장이 한 문구로 다 들어가면 그대로 띄운다.
        sentences: list[list[int]] = []
        sentence: list[int] = []
        for index in range(len(words)):
            sentence.append(index)
            if text_of(index, index).endswith(_SENTENCE_END):
                sentences.append(sentence)
                sentence = []
        if sentence:
            sentences.append(sentence)

        groups: list[list[int]] = []
        for sentence in sentences:
            length = len(text_of(sentence[0], sentence[-1]))
            parts = max(1, -(-length // _PHRASE_CHARS))
            if parts == 1 or len(sentence) < parts:
                groups.append(sentence)
                continue
            # 긴 문장은 균등하게 나눈다. 앞에서부터 한도까지 채우면 꼬리에
            # "분위기입니다." 한 조각만 남아 화면에 글자 몇 개가 덩그러니 뜬다.
            budget = length / parts
            group, cut = [], budget
            for index in sentence:
                group.append(index)
                if index == sentence[-1] or not breakable(index):
                    continue
                so_far = len(text_of(sentence[0], index))
                tail = text_of(group[0], index)
                if tail.endswith(_BINDING_END):
                    continue
                # 쉼표·연결어미는 말하는 사람이 이미 쉬는 자리다. 한도에 조금 못
                # 미쳐도 거기서 끊는 편이 "…전망일 뿐, 정해진"보다 자연스럽다.
                at_pause = ((tail.endswith(",") or _CLAUSE_END.search(tail))
                            and so_far >= cut - budget * .4)
                if so_far >= cut or at_pause:
                    groups.append(group)
                    group, cut = [], max(cut, so_far) + budget
            if group:
                groups.append(group)
        counts.append(len(groups))
        for position, group in enumerate(groups):
            lead = SCENE_LEAD if position == 0 else CAPTION_LEAD
            marked.append((max(0.0, words[group[0]].start - lead),
                           text_of(group[0], group[-1])))

    for (previous, _), (following, _) in zip(marked, marked[1:]):
        if following <= previous:
            raise RenderError("연속 음성에서 찾은 자막 시작 시각이 겹칩니다")
    stops = [start for start, _ in marked[1:]] + [duration]

    phrases: list[tuple[Phrase, ...]] = []
    position = 0
    for count in counts:
        phrases.append(tuple(
            Phrase(marked[position + n][0], stops[position + n], marked[position + n][1])
            for n in range(count)
        ))
        position += count
    return tuple(phrases)


def _scene_durations(scenes: Sequence[Sequence[Phrase]], duration: float) -> list[float]:
    """장면은 자기 첫 자막이 뜨는 순간 바뀐다 — 화면과 글자가 같이 넘어간다."""
    starts = [0.0] + [scene[0].start for scene in scenes[1:]]
    return [stop - start for start, stop in zip(starts, starts[1:] + [duration])]


def _caption_lines(draw, text: str, font: ImageFont.FreeTypeFont) -> list[str]:
    """자막 한 덩어리를 폭에 맞추되 줄 길이를 고르게 나눈다.

    앞줄을 끝까지 채우면 "…전망일" / "뿐," 처럼 뒷줄에 한 어절만 남는다. 줄 수가
    늘지 않는 선까지 폭을 좁혀 보면 같은 줄 수로 가장 고르게 나뉜 자리가 나온다.
    """
    lines = _wrap(draw, text, font, CAPTION_WIDTH)
    narrow = CAPTION_WIDTH
    while len(lines) > 1 and narrow > 160:
        candidate = _wrap(draw, text, font, narrow - 20)
        if len(candidate) != len(lines):
            break
        narrow -= 20
        lines = candidate
    return lines


def _write_captions(scenes: Sequence[Sequence[Phrase]], path: Path, *, font_path: Path) -> None:
    """자막 파일. 줄바꿈은 여기서 어절 경계에 넣는다(`CAPTION_WIDTH` 참고)."""
    def stamp(seconds: float) -> str:
        ms = round(seconds * 1000)
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    font = _font(font_path, CAPTION_FONT_SIZE)
    blocks = [
        f"{index}\n{stamp(phrase.start)} --> {stamp(phrase.end)}\n"
        + "\n".join(_caption_lines(draw, phrase.text, font)) + "\n"
        for index, phrase in enumerate((p for scene in scenes for p in scene), start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


Beat = tuple[str, float, Scene, "int | None"]


def _countup(scene: Scene, seconds: float, *, shown: int) -> list[Beat]:
    """새로 뜬 선택지의 확률이 0에서 제자리까지 차오르는 정지 프레임들.

    올라가는 동안에는 정수만 보여 준다 — 소수점 둘째 자리까지 흔들리면 읽히지
    않고 어지럽기만 하다. 확정된 값은 마지막 프레임에 한 번 제대로 선다.
    숫자로 읽히지 않는 수치나 짧은 구간은 그대로 한 장이다.
    """
    beat = f"option-{shown}"
    label, percent, probability = scene.options[shown - 1]
    match = _METRIC_NUMBER.fullmatch(percent.strip())
    if not match or seconds < COUNTUP_MIN_BEAT:
        return [(beat, seconds, scene, shown)]
    target, unit = float(match[1]), match[2]
    step = COUNTUP_SECONDS / COUNTUP_STEPS
    rising = [
        (beat, step, replace(scene, options=(
            scene.options[:shown - 1]
            + ((label, f"{target * n / COUNTUP_STEPS:.0f}{unit}", probability * n / COUNTUP_STEPS),)
            + scene.options[shown:]
        )), shown)
        for n in range(COUNTUP_STEPS)
    ]
    return rising + [(beat, seconds - COUNTUP_SECONDS, scene, shown)]


def _beats(scene: Scene, seconds: float) -> list[Beat]:
    """한 장면을 화면이 바뀌는 순간들로 나눈다.

    선택지가 있는 장면은 질문만 선 화면으로 열고, 선택지가 말의 순서대로 하나씩
    쌓인다. 도입·마무리는 제목만 먼저 세운 뒤 문구를 얹는다 — 한 장을 20초씩
    그대로 두면 화면이 멈춘 것처럼 보인다.
    """
    if scene.options:
        opening = min(3.2, seconds * .35)
        beats: list[Beat] = [("question", opening, scene, 0)]
        share = (seconds - opening) / len(scene.options)
        for shown in range(1, len(scene.options) + 1):
            beats.extend(_countup(scene, share, shown=shown))
        return beats
    if seconds < 2 * COUNTUP_MIN_BEAT:
        return [("card", seconds, scene, None)]
    opening = min(2.2, seconds * .3)
    return [("open", opening, replace(scene, body=""), None), ("card", seconds - opening, scene, None)]


def render_video(
    scenario: Scenario,
    *,
    audio_path: Path,
    scene_words: Sequence[Sequence[Word]],
    output_path: Path,
    work_dir: Path,
    font_path: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    max_duration: float,
    background_paths: tuple[Path | None, ...] | None = None,
) -> float:
    # 목표 길이는 편집 참고값이다. 음성 전체와 마지막 여운을 먼저 보존한다.
    if background_paths and any(path and path.suffix == ".mp4" for path in background_paths):
        from .clip_render import render_video as render_clips

        return render_clips(
            scenario, audio_path=audio_path, scene_words=scene_words, output_path=output_path,
            work_dir=work_dir, font_path=font_path, ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin,
            max_duration=max_duration, background_paths=background_paths,
        )
    duration = probe_duration(audio_path, ffprobe_bin=ffprobe_bin) + 0.6
    scene_phrases = _phrases([scene.narration for scene in scenario.scenes], scene_words, duration)
    scene_durations = _scene_durations(scene_phrases, duration)
    captions = work_dir / "phrases.srt"
    _write_captions(scene_phrases, captions, font_path=font_path)
    frames, holds, timeline = [], [], []
    selected = background_paths or tuple(None for _ in scenario.scenes)
    if len(selected) != len(scenario.scenes):
        raise RenderError("배경 수와 장면 수가 다릅니다")
    cursor, merging = 0.0, None
    for index, (scene, seconds) in enumerate(zip(scenario.scenes, scene_durations), start=1):
        for position, (beat, hold, display_scene, shown) in enumerate(_beats(scene, seconds), start=1):
            frame = work_dir / f"frame-{index:02d}-{position:02d}-{beat}.png"
            render_frame(display_scene, frame, font_path=font_path, index=index, total=len(scenario.scenes),
                         background_path=selected[index - 1], shown=shown)
            frames.append(frame)
            holds.append(hold)
            # 카운트업은 한 프레임씩 기록하지 않는다 — 검수자가 보는 것은 수치가
            # 머무는 구간이지 그 안의 정지 화면 여덟 장이 아니다. 앞 장면과 제목이
            # 같을 수 있으므로(도입 제목 = 첫 이슈 제목) 장면 번호로 구분한다.
            if merging == (index, beat):
                timeline[-1]["duration"] = round(timeline[-1]["duration"] + hold, 3)
            else:
                timeline.append({"start": round(cursor, 3), "duration": round(hold, 3),
                                 "scene": scene.title, "beat": beat})
            merging = (index, beat)
            cursor += hold
    frame_concat = work_dir / "frames.txt"
    _concat_file(frames, holds, frame_concat)
    # Static layers are already composited by render_frame. Two sparse image streams
    # feeding fps/overlay queued gigabytes of frames on the production FFmpeg build.
    filters = ",".join((
        "fps=30",
        # 프레임 전체를 아주 조금 잘라 내고 그 안에서 천천히 흘린다. 자막은 이
        # 다음에 얹으므로 제자리에 고정된다.
        _drift_filter(),
        _subtitle_filter(captions, font_path.stem),
        "tpad=stop_mode=clone:stop_duration=1",
    ))
    command = [
        ffmpeg_bin, "-y", "-threads", "1", "-f", "concat", "-safe", "0", "-i", str(frame_concat),
        "-i", str(audio_path), "-filter_threads", "1", "-vf", filters, "-map", "0:v", "-map", "1:a",
        "-r", "30", "-c:v", "libx264", "-threads", "2", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-af", "apad=pad_dur=0.6",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode or not output_path.is_file():
        raise RenderError(f"ffmpeg 렌더링 실패: {(result.stderr or result.stdout)[-1000:]}")
    output_path.with_suffix(".timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return duration
