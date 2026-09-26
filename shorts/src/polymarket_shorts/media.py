from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import html
import io
import logging
from pathlib import Path
import re
from typing import Any

from PIL import Image
import requests

from .scenario import Scene


logger = logging.getLogger(__name__)
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_ALLOWED_LICENSES = ("cc0", "public domain", "cc by 4", "cc by 3", "cc by 2")
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


# 저장 배경(provenance.json)과 같은 결을 유지한다: 어두운 톤, 가운데 70%는 비워
# 글자 패널 자리를 남기고, 글자·로고·국기·인물은 넣지 않는다. 인물은 실존 인물
# 얼굴이 그려지는 것을 막으려는 것이다 — 원제에 정치인 이름이 자주 들어간다.
# 실측(2026-09-26)으로 고친 문구다. "가운데 70%를 비워라"는 주제가 사라진 빈 청록
# 화면을 냈고(가독성은 render의 스크림이 맡는다), "Korean … video"나 이슈 원제를
# 그대로 넣으면 제목·한글을 그림 속 글자로 그렸다. 주제는 원고가 쓴 사물·풍경 묘사다.
_BACKGROUND_PROMPT = (
    "A wordless cinematic 3D illustration of {subject}. Main subject large and clearly "
    "visible, centered in the lower half, rich detail and depth, open evening sky above. "
    "Dramatic dusk lighting, deep teal, warm gold and slate blue palette. Purely visual image "
    "with no writing anywhere: no text, letters, signage, banners, flags, logos, numbers or people."
)
WIDTH, HEIGHT = 1080, 1920


def _generate_background(subject: str, target: Path, settings: Any) -> Path | None:
    """Cloudflare Workers AI로 세로 배경 한 장을 만든다. 실패하면 None(호출자가 저장 배경으로)."""
    if not settings.editor_account_id or not settings.editor_api_token:
        return None
    url = (f"https://api.cloudflare.com/client/v4/accounts/{settings.editor_account_id}"
           f"/ai/run/{settings.image_model}")
    try:
        response = requests.post(
            url, headers={"Authorization": f"Bearer {settings.editor_api_token}"},
            json={"prompt": _BACKGROUND_PROMPT.format(subject=subject), "steps": 8},
            timeout=(10, 90),
        )
        response.raise_for_status()
        encoded = response.json()["result"]["image"]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
            image = image.convert("RGB")
            # flux-1-schnell은 정사각형만 준다. 가운데를 9:16으로 잘라 세로 화면에 맞춘다.
            width, height = image.size
            crop = min(width, round(height * WIDTH / HEIGHT))
            left = (width - crop) // 2
            image = image.crop((left, 0, left + crop, height)).resize((WIDTH, HEIGHT), Image.LANCZOS)
            image.save(target, format="PNG")
        return target
    except (requests.RequestException, KeyError, TypeError, ValueError, OSError) as exc:
        logger.warning("배경 생성 실패, 저장 배경을 쓴다: %s (%s)", subject[:60], exc)
        return None


def backgrounds_for(scenes: tuple[Scene, ...], work_dir: Path, settings: Any) -> tuple[Path | None, ...]:
    """장면별 배경. 이슈 장면은 그날 이슈로 새로 그리고, 도입은 첫 이슈 그림을 함께 쓴다.

    마무리 고지와 생성 실패는 저장 배경(`background_for`)이다 — 배경 한 장 때문에
    그날 제작을 멈추지 않는다.
    """
    first = next((scene.visual_query for scene in scenes if scene.kind == "consensus"), "")
    made: dict[str, Path | None] = {}
    chosen: list[Path | None] = []
    for scene in scenes:
        query = first if scene.kind == "intro" else scene.visual_query
        path = None
        if settings.generated_backgrounds and scene.kind != "outro" and "topic:" in query:
            if query not in made:
                # 이슈별 파일 이름을 고정해 두면 검수 중 수정·재렌더가 같은 그림을 다시 쓴다.
                target = work_dir / f"{hashlib.sha1(query.encode()).hexdigest()[:12]}.png"
                if target.is_file():
                    made[query] = target
                else:
                    work_dir.mkdir(parents=True, exist_ok=True)
                    made[query] = _generate_background(query.split("topic:", 1)[1].strip(), target, settings)
            path = made[query]
        chosen.append(path or background_for(scene.kind, scene.visual_query))
    return tuple(chosen)


@dataclass(frozen=True)
class VisualCredit:
    scene: str
    title: str
    creator: str
    license: str
    license_url: str
    source_url: str


@dataclass(frozen=True)
class VisualSet:
    paths: tuple[Path | None, ...]
    credits: tuple[VisualCredit, ...]

    def credits_payload(self) -> list[dict[str, str]]:
        return [asdict(credit) for credit in self.credits]


def _plain(value: Any) -> str:
    raw = str((value or {}).get("value") if isinstance(value, dict) else value or "")
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def _license_allowed(value: str) -> bool:
    normalized = value.strip().lower()
    if any(blocked in normalized for blocked in ("-sa", "sharealike", "-nc", "-nd")):
        return False
    return any(token in normalized for token in _ALLOWED_LICENSES)


class CommonsImageProvider:
    """키 없이 Wikimedia Commons의 자유 라이선스 이미지만 내려받는다."""

    def __init__(self, *, session: Any | None = None, timeout: float = 20):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.headers = {
            "User-Agent": "PolymarketShorts/0.2 (https://nunchi.live; daily video renderer)"
        }

    def _candidates(self, query: str) -> list[dict[str, str]]:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": 6,
            "gsrlimit": 16,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": 1600,
            "iiextmetadatafilter": "Artist|Credit|LicenseShortName|LicenseUrl",
            "iiextmetadatalanguage": "en",
        }
        response = self.session.get(
            COMMONS_API, params=params, headers=self.headers, timeout=self.timeout
        )
        response.raise_for_status()
        pages = ((response.json().get("query") or {}).get("pages") or {}).values()
        candidates: list[dict[str, str]] = []
        for page in pages:
            info_rows = page.get("imageinfo") if isinstance(page, dict) else None
            info = info_rows[0] if isinstance(info_rows, list) and info_rows else {}
            metadata = info.get("extmetadata") or {}
            license_name = _plain(metadata.get("LicenseShortName"))
            mime = str(info.get("mime") or "")
            width = int(info.get("thumbwidth") or info.get("width") or 0)
            height = int(info.get("thumbheight") or info.get("height") or 0)
            download_url = str(info.get("thumburl") or info.get("url") or "")
            if (
                not _license_allowed(license_name)
                or not mime.startswith("image/")
                or width < 800
                or height < 500
                or not download_url.startswith("https://")
            ):
                continue
            candidates.append(
                {
                    "title": str(page.get("title") or "Wikimedia Commons image"),
                    "creator": _plain(metadata.get("Artist") or metadata.get("Credit")) or "Unknown",
                    "license": license_name,
                    "license_url": _plain(metadata.get("LicenseUrl")),
                    "source_url": str(info.get("descriptionurl") or ""),
                    "download_url": download_url,
                }
            )
        return candidates

    def download(self, query: str, target: Path, *, seed: str, scene: str) -> VisualCredit | None:
        try:
            candidates = self._candidates(query)
            if not candidates:
                return None
            digest = hashlib.sha256(f"{seed}:{query}".encode()).digest()
            selected = candidates[int.from_bytes(digest[:4], "big") % len(candidates)]
            response = self.session.get(
                selected["download_url"], headers=self.headers, timeout=self.timeout
            )
            response.raise_for_status()
            if len(response.content) > 15 * 1024 * 1024:
                raise ValueError("image exceeds 15 MiB")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)
            with Image.open(target) as image:
                image.verify()
            return VisualCredit(
                scene=scene,
                title=selected["title"],
                creator=selected["creator"],
                license=selected["license"],
                license_url=selected["license_url"],
                source_url=selected["source_url"],
            )
        except (requests.RequestException, ValueError, OSError) as exc:
            logger.warning("무료 이미지 수집 실패 query=%s: %s", query, exc)
            target.unlink(missing_ok=True)
            return None

    def for_scenes(self, scenes: tuple[Scene, ...], root: Path, *, seed: str) -> VisualSet:
        paths: list[Path | None] = []
        credits: list[VisualCredit] = []
        for index, scene in enumerate(scenes, start=1):
            target = root / f"visual-{index:02d}.jpg"
            credit = self.download(
                scene.visual_query,
                target,
                seed=seed,
                scene=scene.title,
            )
            paths.append(target if credit else None)
            if credit:
                credits.append(credit)
        return VisualSet(paths=tuple(paths), credits=tuple(credits))


def empty_visuals(scenes: tuple[Scene, ...]) -> VisualSet:
    return VisualSet(paths=tuple(None for _ in scenes), credits=())
