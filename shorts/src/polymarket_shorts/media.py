from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import html
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
