import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from telegram_bot.core.storage import write_json_atomic


class WatchlistManager:
    """관심종목 관리"""

    def __init__(
        self,
        file_path: Path,
        code_resolver: Callable[[str], str | None] | None = None,
    ):
        self._file_path = file_path
        self._code_resolver = code_resolver
        self._watchlist: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self):
        if not self._file_path.exists():
            self._watchlist = {}
            write_json_atomic(self._file_path, self._watchlist, indent=2)
        else:
            self._watchlist = json.loads(self._file_path.read_text(encoding="utf-8"))

    def _canonical_code(self, code: str) -> str:
        if self._code_resolver is None:
            return code
        return self._code_resolver(code) or code

    async def _persist(self, watchlist: dict[str, str]) -> None:
        """저장이 끝난 목록만 메모리에 올린다.

        먼저 메모리를 고치고 저장하면, 저장이 실패한 뒤에도 봇은 바뀐 목록으로
        동작하다가 다음 저장 때 그 변경을 함께 적어 버린다 — 사용자에게는
        실패라고 알린 편입·편출이 뒤늦게 반영되는 셈이다.
        """
        await asyncio.to_thread(
            write_json_atomic, self._file_path, watchlist, indent=2
        )
        self._watchlist = watchlist

    async def get_all(self) -> dict[str, str]:
        async with self._lock:
            return self._watchlist.copy()

    async def add(self, code: str, name: str) -> None:
        async with self._lock:
            updated = dict(self._watchlist)
            updated[self._canonical_code(code)] = name
            await self._persist(updated)

    async def remove(self, code: str) -> str | None:
        async with self._lock:
            updated = dict(self._watchlist)
            name = updated.pop(self._canonical_code(code), None)
            if name is not None:
                await self._persist(updated)
            return name
