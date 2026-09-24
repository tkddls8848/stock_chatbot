"""관심종목 — 공유 저장소의 `storage/portfolio/watchlist.json` 한 벌.

사람의 추가·삭제는 웹 개인 화면(`/portfolio`)이 하고, 봇은 수집·사전선별·리서치·
브리핑에서 같은 파일을 직접 읽는다. 사본도 HTTP 동기화도 없다. 봇이 쓰는 것은
리서치 자동 적용뿐이고, 웹과 쓰는 쪽이 둘이라 잠금 파일을 잡고 **다시 읽은 뒤**
고쳐 쓴다(`code_guide.md`의 「공유 저장소」 예외).

형식은 `{canonical_code: name}`이다. 웹의 `services/web/portfolio/`가 같은 형식으로
읽고 쓴다 — 바꾸면 양쪽 테스트를 같은 커밋에서 고친다.
"""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from services.telegram_bot.core.storage import file_lock, write_json_atomic


class WatchlistManager:
    """관심종목 읽기와 리서치 적용."""

    def __init__(
        self,
        file_path: Path,
        code_resolver: Callable[[str], str | None] | None = None,
        lock_path: Path | None = None,
    ):
        self._file_path = file_path
        self._lock_path = lock_path or file_path.with_name(file_path.name + ".lock")
        self._code_resolver = code_resolver
        self._watchlist: dict[str, str] = {}
        self._lock = asyncio.Lock()
        # 마지막 읽기의 문제. /system이 보여 준다 — 깨진 파일을 빈 목록으로 삼키지 않는다.
        self.last_error: str | None = None
        self._refresh()

    def _read(self) -> dict[str, str]:
        """파일을 읽는다. 없으면 빈 목록(새 설치), 깨졌으면 예외."""
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
        ):
            raise ValueError("관심종목 파일 형식이 {코드: 이름}이 아니다")
        return raw

    def _refresh(self) -> dict[str, str]:
        """다시 읽는다. 깨졌으면 마지막으로 읽은 목록을 그대로 쓰고 오류를 남긴다."""
        try:
            self._watchlist = self._read()
            self.last_error = None
        except (OSError, ValueError) as error:
            self.last_error = f"{type(error).__name__}: {error}"
        return dict(self._watchlist)

    def _canonical_code(self, code: str) -> str:
        if self._code_resolver is None:
            return code
        return self._code_resolver(code) or code

    def _update(self, change: Callable[[dict[str, str]], object]) -> object:
        """잠금 → 다시 읽기 → 고치기 → 원자적 쓰기. 웹이 방금 쓴 것을 덮지 않는다."""
        with file_lock(self._lock_path):
            current = self._read()
            result = change(current)
            write_json_atomic(self._file_path, current, indent=2)
        self._watchlist = current
        self.last_error = None
        return result

    async def get_all(self) -> dict[str, str]:
        async with self._lock:
            return await asyncio.to_thread(self._refresh)

    async def add(self, code: str, name: str) -> None:
        canonical = self._canonical_code(code)
        async with self._lock:
            await asyncio.to_thread(self._update, lambda items: items.__setitem__(canonical, name))

    async def remove(self, code: str) -> str | None:
        canonical = self._canonical_code(code)
        async with self._lock:
            return await asyncio.to_thread(self._update, lambda items: items.pop(canonical, None))

    def status_line(self) -> str:
        if self.last_error:
            return f"관심종목 파일 읽기 실패(마지막 목록 {len(self._watchlist)}개로 동작): {self.last_error}"
        return f"관심종목 {len(self._watchlist)}개 · {self._file_path}"
