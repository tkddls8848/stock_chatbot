"""계정 디렉터리의 자산·관심종목·진단 저장소. 공개 라우트와 봇은 읽지 않는다."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from services.web.core.clock import now
from services.web.core.storage import file_lock, write_json_atomic


class StoreError(RuntimeError):
    """파일이 깨져 읽을 수 없다. 빈 목록으로 삼키지 않고 호출자에게 알린다."""


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as error:
        raise StoreError(f"{path.name}: {error}") from error


# ── 자산 ────────────────────────────────────────────────────────────────────

class AssetStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock_path = path.with_name(path.name + ".lock")

    def list(self) -> list[dict[str, Any]]:
        payload = _read_json(self._path, {"assets": []})
        assets = payload.get("assets") if isinstance(payload, dict) else None
        if not isinstance(assets, list):
            raise StoreError("assets.json 형식이 {assets: [...]}가 아니다")
        return assets

    def _write(self, assets: list[dict[str, Any]]) -> None:
        write_json_atomic(
            self._path,
            {"updated_at": now().isoformat(timespec="seconds"), "assets": assets},
            indent=2,
        )

    def add(self, asset: dict[str, Any], *, limit: int) -> dict[str, Any]:
        with file_lock(self._lock_path):
            assets = self.list()
            if len(assets) >= limit:
                raise ValueError(f"자산은 {limit}개까지 저장한다")
            row = {**asset, "id": uuid.uuid4().hex[:12], "updated_at": now().isoformat(timespec="seconds")}
            assets.append(row)
            self._write(assets)
        return row

    def replace(self, asset_id: str, asset: dict[str, Any]) -> dict[str, Any] | None:
        with file_lock(self._lock_path):
            assets = self.list()
            for index, row in enumerate(assets):
                if row.get("id") == asset_id:
                    assets[index] = {**asset, "id": asset_id,
                                     "updated_at": now().isoformat(timespec="seconds")}
                    self._write(assets)
                    return assets[index]
        return None

    def delete(self, asset_id: str) -> bool:
        with file_lock(self._lock_path):
            assets = self.list()
            kept = [row for row in assets if row.get("id") != asset_id]
            if len(kept) == len(assets):
                return False
            self._write(kept)
        return True


# ── 계정별 관심종목 ────────────────────────────────────────────────────

_US_TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")
# 거래소 선택지. 봇의 종목 DB(`stocks/universe.py`의 `stock_key`)와 같은 정규 코드를 만든다.
EXCHANGES = {
    "CN": ("SH", "SZ"),
    "HK": ("HKEX",),
    "KR": ("KOSPI", "KOSDAQ"),
    "US": ("NASDAQ", "NYSE"),
}


def canonical_code(market: str, exchange: str, raw: str) -> str | None:
    """관심종목 파일에 저장하는 정규 코드. CN·HK는 숫자만, KR·US는 거래소를 붙인다.

    KR 6자리는 A주 코드와 겹치므로 KR·US에만 거래소를 붙인다(`code_guide.md`).
    """
    market = market.strip().upper()
    exchange = exchange.strip().upper()
    value = raw.strip().upper()
    if exchange not in EXCHANGES.get(market, ()):
        return None
    if market == "CN":
        return value if value.isdigit() and len(value) == 6 else None
    if market == "HK":
        return value.zfill(5) if value.isdigit() and len(value) <= 5 else None
    if market == "KR":
        return f"KR:{exchange}:{value.zfill(6)}" if value.isdigit() and len(value) <= 6 else None
    if market == "US":
        return f"US:{exchange}:{value}" if _US_TICKER.fullmatch(value) and not value.isdigit() else None
    return None


class WatchlistStore:
    def __init__(self, path: Path):
        self._path = path
        # 개별 파일 쓰기에도 잠금을 사용한다.
        self._lock_path = path.with_name(path.name + ".lock")

    def get(self) -> dict[str, str]:
        payload = _read_json(self._path, {})
        if not isinstance(payload, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in payload.items()
        ):
            raise StoreError("watchlist.json 형식이 {코드: 이름}이 아니다")
        return payload

    def replace(self, items: dict[str, str]) -> dict[str, str]:
        """전체 교체(`PUT`). 계정 안에서 파일 쓰기가 서로 섞이지 않는다."""
        with file_lock(self._lock_path):
            write_json_atomic(self._path, items, indent=2)
        return items


# ── 조언 ────────────────────────────────────────────────────────────────────

_ADVICE_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")


class AdviceStore:
    def __init__(self, folder: Path):
        self._folder = folder

    def new_id(self) -> str:
        return now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]

    def save(self, advice: dict[str, Any]) -> None:
        write_json_atomic(self._folder / f"{advice['id']}.json", advice, indent=2)
        # 해당 계정의 마지막 진단 스냅샷.
        write_json_atomic(self._folder / "latest.json", advice, indent=2)

    def ids(self) -> list[str]:
        if not self._folder.is_dir():
            return []
        return sorted(
            (p.stem for p in self._folder.glob("*.json") if _ADVICE_ID.fullmatch(p.stem)),
            reverse=True,
        )

    def get(self, advice_id: str) -> dict[str, Any] | None:
        if not _ADVICE_ID.fullmatch(advice_id):
            return None
        value = _read_json(self._folder / f"{advice_id}.json", None)
        return value if isinstance(value, dict) else None

    def latest(self) -> dict[str, Any] | None:
        ids = self.ids()
        return self.get(ids[0]) if ids else None

    def count_on(self, day: str) -> int:
        """한국 시간 달력 하루(`YYYYMMDD`)에 만든 조언 수. 하루 상한에 쓴다."""
        return sum(1 for advice_id in self.ids() if advice_id.startswith(day))

    def prune(self, keep: int) -> None:
        for advice_id in self.ids()[keep:]:
            (self._folder / f"{advice_id}.json").unlink(missing_ok=True)
