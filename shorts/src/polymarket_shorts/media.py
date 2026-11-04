from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image



logger = logging.getLogger(__name__)
ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "backgrounds"


# `visual_query`가 가리키는 장면 성격을 저장된 두 배경 중 하나로 옮긴다. 예전에는
# "shipping" 한 단어만 봐서 지정학·마무리까지 전부 같은 도시 야경으로 떨어졌다.
# 장면마다 달라 보이게 하는 나머지(크롭·방향·색조)는 `render._background`가 한다.
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


WIDTH, HEIGHT = 1080, 1920


