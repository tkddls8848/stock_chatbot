"""쇼츠 CLI를 하위 프로세스로 부른다. 봇은 쇼츠 패키지를 import하지 않는다.

계약은 `python -m polymarket_shorts.cli`의 명령과 stdout JSON 한 줄이다
(`--status`·`--edit TEXT`·`--complete`, 제작은 인자 없이 또는 `--force`).
환경은 **최소한만** 넘긴다 — 봇의 텔레그램 토큰·자격증명을 쇼츠에 흘리지 않고,
쇼츠는 자기 `shorts/.env`를 스스로 읽는다. `STORAGE_DIR`만은 봇과 같은 값을 넘겨
두 프로세스가 같은 공유 저장소를 보게 한다.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from services.telegram_bot.core.config import SHORTS_PYTHON, SHORTS_WORKDIR, STORAGE_DIR

_PASSED_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
_MAX_ERROR_CHARS = 400


class ShortsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShortsRunner:
    python: str = str(SHORTS_PYTHON)
    workdir: str = str(SHORTS_WORKDIR)
    storage_dir: str = str(STORAGE_DIR)

    def _env(self) -> dict[str, str]:
        env = {key: os.environ[key] for key in _PASSED_ENV if key in os.environ}
        env["STORAGE_DIR"] = self.storage_dir
        env["PYTHONUNBUFFERED"] = "1"
        return env

    async def call(self, args: list[str], *, timeout: float) -> dict[str, Any]:
        if not os.path.exists(self.python):
            raise ShortsError(f"쇼츠 venv가 없습니다: {self.python}")
        process = await asyncio.create_subprocess_exec(
            self.python, "-m", "polymarket_shorts.cli", *args,
            cwd=self.workdir, env=self._env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise ShortsError(f"{int(timeout // 60)}분 안에 끝나지 않아 멈췄습니다") from None
        if process.returncode != 0:
            detail = stderr.decode("utf-8", "replace").strip().splitlines()
            raise ShortsError((detail[-1] if detail else f"종료 코드 {process.returncode}")[:_MAX_ERROR_CHARS])
        lines = [line for line in stdout.decode("utf-8", "replace").splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1]) if lines else {}
        except ValueError as error:
            raise ShortsError("쇼츠 CLI 응답이 JSON이 아닙니다") from error
        if not isinstance(payload, dict):
            raise ShortsError("쇼츠 CLI 응답이 객체가 아닙니다")
        return payload
