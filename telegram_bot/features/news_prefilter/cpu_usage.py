"""사전선별 학습과 봇 foreground의 일별 CPU 사용량."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from telegram_bot.core.clock import now
from telegram_bot.core.storage import write_json_atomic


class CpuUsage:
    """봇 foreground와 로컬 학습의 UTC 일별 CPU 사용량. 일일 제한은 없다."""

    def __init__(self, state_file: Path):
        self._state_file = state_file
        self._state = self._load_state()
        self._last_process_cpu = time.process_time()
        self._background_since_checkpoint = 0.0

    @staticmethod
    def _utc_day() -> str:
        # Neurons 예산과 같은 UTC 00시 리셋 — core/clock.py의 now()/today()(JST)를
        # 쓰지 않는 명시적 예외. 날짜 경계를 맞춰야 Neurons가 소진된 날을
        # 다른 쪽 로그와 같은 일자로 읽을 수 있다.
        return datetime.now(timezone.utc).date().isoformat()

    def _load_state(self) -> dict[str, float | str]:
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            raw = {}
        day = self._utc_day()
        if not isinstance(raw, dict) or raw.get("utc_day") != day:
            return {
                "utc_day": day,
                "foreground_cpu_seconds": 0.0,
                "background_cpu_seconds": 0.0,
            }
        return {
            "utc_day": day,
            "foreground_cpu_seconds": float(raw.get("foreground_cpu_seconds") or 0.0),
            "background_cpu_seconds": float(raw.get("background_cpu_seconds") or 0.0),
        }

    def _reset_day_if_needed(self) -> None:
        day = self._utc_day()
        if self._state["utc_day"] == day:
            return
        self._state = {
            "utc_day": day,
            "foreground_cpu_seconds": 0.0,
            "background_cpu_seconds": 0.0,
        }
        self._last_process_cpu = time.process_time()
        self._background_since_checkpoint = 0.0

    def _persist(self) -> None:
        payload = dict(self._state)
        payload["updated_at"] = now().isoformat(timespec="seconds")
        write_json_atomic(self._state_file, payload)

    def account_foreground_cpu(self) -> float:
        """직전 체크포인트 이후의 foreground CPU초를 기록하고 반환한다."""
        self._reset_day_if_needed()
        current = time.process_time()
        delta = max(0.0, current - self._last_process_cpu)
        foreground = max(0.0, delta - self._background_since_checkpoint)
        self._state["foreground_cpu_seconds"] += foreground
        self._last_process_cpu = current
        self._background_since_checkpoint = 0.0
        self._persist()
        return foreground

    def record_background_cpu(self, cpu_seconds: float) -> None:
        self._reset_day_if_needed()
        used = max(0.0, float(cpu_seconds))
        self._state["background_cpu_seconds"] += used
        self._background_since_checkpoint += used
        self._persist()

    def status(self) -> dict[str, float | str]:
        self._reset_day_if_needed()
        return {
            "utc_day": self._state["utc_day"],
            "used_seconds": float(self._state["background_cpu_seconds"]),
            "foreground_seconds": float(self._state["foreground_cpu_seconds"]),
        }
