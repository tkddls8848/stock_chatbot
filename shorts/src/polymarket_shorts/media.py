"""영상에 넣는 그림.

질문마다 그 주제를 상징하는 그림을 그려 화면 위쪽 그림 창에 넣고, 추적 코너는 질문을 여는 전체 화면 컷에도 쓴다
(운영자 결정 2026-11-10, 화면은 `render.py`). 그림은 숫자·자막과 겹치지 않는 자리에만 놓인다 — 예전에 그림을 화면
전체 배경으로 깔았을 때는 그 위의 글자가 흐려져 걷어냈다(2026-10-08).

그림은 장식이다. 그리지 못한 장면은 그림 없는 지금의 화면으로 나가고 제작은 멈추지 않는다.
"""

from __future__ import annotations

import base64
from dataclasses import replace
import io
import logging
from pathlib import Path
import time
from typing import Any

from PIL import Image
import requests

from .config import Settings
from .render import LIST_PICTURE_ROWS
from .scenario import Scenario, Scene
from .watch import watch_dir


logger = logging.getLogger(__name__)
ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "backgrounds"


# `visual_query`가 가리키는 장면 성격을 저장된 두 배경 중 하나로 옮긴다. 예전에는
# "shipping" 한 단어만 봐서 지정학·마무리까지 전부 같은 도시 야경으로 떨어졌다.
# HyperFrames 내보내기만 쓴다.
_TRADE_HINTS = ("shipping", "cargo", "container", "trade", "united nations", "security council")


def background_for(kind: str, visual_query: str, root: Path = ASSET_DIR) -> Path | None:
    """Select a pre-generated local image without network or generation calls."""
    query = visual_query.lower()
    trade = kind == "outro" or (kind == "consensus" and any(hint in query for hint in _TRADE_HINTS))
    path = root / ("global-trade.png" if trade else "financial-city.png")
    try:
        with Image.open(path) as image:
            image.verify()
        return path
    except (OSError, ValueError) as exc:
        logger.warning("로컬 배경을 읽을 수 없어 기본 배경 사용: %s (%s)", path, exc)
        return None


# 그림 창은 가로로 긴 띠이고 여는 컷은 세로 화면이다. 한 장을 잘라 두 자리에 쓰지 않고 자리마다 제 비율로 그린다 —
# 정사각형 한 장의 가운데를 자르면 띠에는 대상의 허리만, 세로에는 양옆이 잘린 대상이 남았다(실측 2026-11-10).
# 모델이 받는 크기는 16의 배수다.
WINDOW_SIZE = (1248, 400)
CUT_SIZE = (720, 1280)
# 주제는 원고가 쓴 사물·풍경 묘사(`image_scene`)다. 글자·로고·국기·사람은 넣지 않는다 — 사람은 실존 인물의 얼굴이
# 그려지는 것을 막으려는 것이다(원제에 정치인 이름이 자주 들어간다). 색은 화면의 남색 바탕에 맞춘다.
_PROMPT = (
    "A wordless cinematic 3D illustration of {subject}. Main subject large, centered and clearly "
    "visible, rich detail and depth. Dramatic dusk lighting, deep navy, teal and warm gold palette. "
    "Purely visual image with no writing anywhere: no text, letters, signage, banners, flags, logos, "
    "numbers or people."
)
# 그림 모델은 "사람·글자 금지"를 가끔 어긴다(실측 2026-09-27: 연준 그림에 노트북 앞 인물, 2026-11-10: 현판에
# "CENTRAL BALL BANK IT"). 그린 뒤 비전 모델로 보고 걸리면 다시 그린다. 끝까지 걸리면 그 장면은 그림 없이 간다.
_CHECKS = (
    ("사람", "Is there any person, human figure or human face in this image? Answer yes or no."),
    ("글자", "Is there any written text, letters, numbers or words in this image? Answer yes or no."),
)
_ATTEMPTS = 3
# 그림 한 장은 약 9초, 검사는 약 3초다(실측 2026-11-10). 모델이 느린 날 그림이 제작 시간(서비스 한도 40분)을
# 먹지 않게, 한 번의 제작이 그림에 쓰는 시간을 묶는다. 넘으면 남은 장면은 그림 없이 간다.
_TIMEOUT = (10, 45)
_BUDGET_SECONDS = 180


def _run(model: str, settings: Settings, **body: Any) -> dict[str, Any]:
    response = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{settings.editor_account_id}/ai/run/{model}",
        headers={"Authorization": f"Bearer {settings.editor_api_token}"}, timeout=_TIMEOUT, **body,
    )
    response.raise_for_status()
    return response.json()["result"]


def _draw(subject: str, size: tuple[int, int], settings: Settings) -> Image.Image:
    # 이 모델은 JSON이 아니라 multipart 양식으로 받는다.
    fields = {"prompt": _PROMPT.format(subject=subject), "width": str(size[0]), "height": str(size[1])}
    result = _run(settings.image_model, settings, files={key: (None, value) for key, value in fields.items()})
    with Image.open(io.BytesIO(base64.b64decode(result["image"]))) as image:
        return image.convert("RGB")


def _flagged(image: Image.Image, settings: Settings) -> list[str]:
    """비전 모델이 사람·글자를 봤으면 그 항목 이름들."""
    small = image.copy()
    small.thumbnail((768, 768))
    buffer = io.BytesIO()
    small.save(buffer, format="JPEG", quality=85)
    found = []
    for name, question in _CHECKS:
        result = _run(settings.vision_model, settings,
                      json={"image": list(buffer.getvalue()), "prompt": question, "max_tokens": 8})
        if not str(result["description"]).strip().lower().startswith("no"):
            found.append(name)
    return found


def _picture(subject: str, target: Path, size: tuple[int, int], settings: Settings, deadline: float) -> str:
    """`target`에 그림 한 장을 둔다. 돌려주는 값은 결과("있던 그림"·"새로 그림") 또는 실패 사유다.

    이미 있는 파일은 다시 그리지 않는다 — 같은 날 다시 제작하거나 수정본을 렌더해도 같은 그림이 나오고, 추적
    주제는 매일 같은 그림으로 열린다. 검사를 못 하면 통과로 보지 않는다.
    """
    if target.is_file():
        return "있던 그림"
    for attempt in range(1, _ATTEMPTS + 1):
        if time.monotonic() > deadline:
            return "실패: 그림에 쓸 시간을 넘김"
        try:
            image = _draw(subject, size, settings)
            flagged = _flagged(image, settings)
        except (requests.RequestException, KeyError, TypeError, ValueError, OSError) as exc:
            logger.warning("그림을 그리지 못했습니다: %s (%s)", subject[:60], exc)
            return f"실패: {str(exc)[:160]}"
        if not flagged:
            target.parent.mkdir(parents=True, exist_ok=True)
            # 같은 폴더의 임시 파일로 쓰고 바꿔 끼운다. 쓰다 만 파일이 남으면 "있던 그림"으로 매일 다시 쓰인다.
            partial = target.with_name(target.name + ".part")
            image.save(partial, format="JPEG", quality=92)
            partial.replace(target)
            return "새로 그림"
        logger.warning("그림에 %s 보여 다시 그립니다(%d/%d): %s", "·".join(flagged), attempt, _ATTEMPTS,
                       subject[:60])
    return f"실패: {_ATTEMPTS}번 모두 사람·글자가 보임"


def _subject(scene: Scene) -> str:
    """장면이 그릴 대상. 원고의 그림 묘사는 `visual_query`의 "topic:" 뒤에 실려 온다(`scenario.py`)."""
    return scene.visual_query.split("topic:", 1)[1].strip() if "topic:" in scene.visual_query else ""


def attach_pictures(scenario: Scenario, day_dir: Path, settings: Settings) -> tuple[Scenario, dict[str, Any]]:
    """장면에 그림 파일을 붙인 시나리오와 감사 기록.

    - 추적 장면: 그림 창용 가로 그림과 여는 컷용 세로 그림. 주제가 추적되는 동안 `watch/pictures/`에 두고 매일 쓴다.
    - 새 이슈 장면: 그림 창용 가로 그림. 그날 폴더에 둔다(보관 기간이 지나면 폴더와 함께 지워진다). 이슈가 많아
      목록 화면에 그림 창을 둘 자리가 없는 날(`render.LIST_PICTURE_ROWS`)은 그리지 않는다.
    """
    if not (settings.visuals_enabled and settings.editor_account_id and settings.editor_api_token):
        return scenario, {"status": "그림을 그리지 않음"}
    started = time.monotonic()
    listed = sum(scene.kind == "consensus" for scene in scenario.scenes) <= LIST_PICTURE_ROWS
    report: list[dict[str, str]] = []
    scenes = []
    for scene in scenario.scenes:
        subject = _subject(scene)
        wanted: dict[str, tuple[Path, tuple[int, int]]] = {}
        if subject and scene.event_id and scene.kind == "watch":
            kept = watch_dir(settings) / "pictures"
            wanted = {"picture": (kept / f"{scene.event_id}-wide.jpg", WINDOW_SIZE),
                      "cut": (kept / f"{scene.event_id}-tall.jpg", CUT_SIZE)}
        elif subject and scene.event_id and scene.kind == "consensus" and listed:
            wanted = {"picture": (day_dir / "pictures" / f"{scene.event_id}-wide.jpg", WINDOW_SIZE)}
        made = {}
        for field, (target, size) in wanted.items():
            outcome = _picture(subject, target, size, settings, started + _BUDGET_SECONDS)
            report.append({"event_id": scene.event_id, "use": field, "outcome": outcome})
            if not outcome.startswith("실패"):
                made[field] = str(target)
        if made:
            # 검수 기록에 그림이 무엇으로 그려졌는지 남긴다.
            scene = replace(scene, **made, evidence=(*scene.evidence, f"그림(AI 생성, 글자·사람 검사 통과): {subject}"))
        scenes.append(scene)
    return replace(scenario, scenes=tuple(scenes)), {
        "model": settings.image_model, "pictures": report, "seconds": round(time.monotonic() - started, 1)}
