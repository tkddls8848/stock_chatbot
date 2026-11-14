"""운영 통계: 고정된 화면/기능의 횟수와 로그인 계정의 활동일. 공개 API는 없다."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
import json
import logging
from pathlib import Path
import re
import threading

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from services.web.core.clock import now
from services.web.core.storage import FileLockTimeout, write_json_atomic

logger = logging.getLogger(__name__)
RETENTION_DAYS = 30
PAGES = {"/": "홈", "/forecast": "예측 컨센서스", "/search": "뉴스 검색",
         "/portfolio": "내 자산", "/research": "리서치", "/about": "소개",
         "/privacy": "개인정보", "/terms": "이용약관"}
ACTIONS = {("GET", "/api/search"): "news_search", ("GET", "/api/forecast/events"): "forecast_list",
           ("POST", "/api/forecast/ask"): "forecast_ask", ("POST", "/api/research/reports"): "research"}
_BOT = re.compile(r"bot|crawler|spider|curl|python|wget|headless|monitor", re.I)
_ACCOUNT = re.compile(r"[0-9a-f]{64}")


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid activity record")
    return value


def record_account(root: Path, *, login: bool = False) -> None:
    """호출자가 계정 잠금을 쥔다. 기존 계정의 가입일은 추측하지 않는다."""
    stamp = now()
    day = stamp.date().isoformat()
    path = root / "activity.json"
    data = _read(path)
    if not data:
        data = {"first_login_at": stamp.isoformat() if login and not root.exists() else None, "days": []}
    cutoff = (stamp.date() - timedelta(days=RETENTION_DAYS - 1)).isoformat()
    days = sorted({date for date in data["days"] if date >= cutoff} | {day})
    if days != data["days"] or login:
        data["days"] = days
        if login:
            data["last_login_at"] = stamp.isoformat()
        write_json_atomic(path, data)


class Activity:
    def __init__(self, root: Path, users: Path):
        self.root, self.users = root, users
        self._lock = threading.RLock()
        try:
            self.data = _read(root / "activity.json") or {"started_at": now().isoformat(), "days": {}}
            if not isinstance(self.data.get("days"), dict) or not isinstance(self.data.get("started_at"), str):
                raise ValueError("Invalid activity record")
        except (OSError, ValueError):
            # 통계 손상 때문에 사이트를 내리지 않는다. 덮어쓰지도 않고 요약의 갱신 시각을 멈춘다.
            self.data = None
            logger.error("웹 활동 통계 기록 오류; 통계 수집 중단")

    def observe(self, request, response, accounts) -> None:
        if self.data is None:
            return
        path = request.url.path
        ua = request.headers.get("user-agent", "")
        if request.method == "HEAD" or not ua or _BOT.search(ua):
            return
        page = request.method == "GET" and path in PAGES
        action = ACTIONS.get((request.method, path))
        private = path.startswith(("/api/portfolio/", "/api/research", "/api/forecast/ask"))
        if not (page or action or private):
            return
        stamp = now().date().isoformat()
        with self._lock:
            row = self.data["days"].setdefault(stamp, {"pages": {}, "actions": {}, "errors": 0})
            if response.status_code >= 500:
                row["errors"] += 1
            if not 200 <= response.status_code < 300:
                return
            if page:
                row["pages"][path] = row["pages"].get(path, 0) + 1
            if action:
                row["actions"][action] = row["actions"].get(action, 0) + 1
        # 계정 잠금 안에서 세션을 다시 확인한다. 탈퇴와 동시에 활동 파일을 되살리지 않는다.
        try:
            with contextmanager(accounts.context)(request) as account:
                record_account(account.root)
        except HTTPException:
            pass

    def flush(self) -> None:
        from services.web.accounts import account_lock

        if self.data is None:
            raise ValueError("Activity storage unavailable")
        stamp = now()
        day = stamp.date()
        cutoff = (day - timedelta(days=RETENTION_DAYS - 1)).isoformat()
        with self._lock:
            self.data["days"] = {key: value for key, value in self.data["days"].items() if key >= cutoff}
            accounts = []
            if self.users.exists():
                for root in self.users.iterdir():
                    if root.is_dir() and not root.is_symlink() and _ACCOUNT.fullmatch(root.name):
                        with account_lock(self.users, root.name, timeout=0.1):
                            if not root.exists():
                                continue
                            data = _read(root / "activity.json")
                            days = [d for d in data.get("days", []) if d >= cutoff]
                            if days != data.get("days", []):
                                data["days"] = days
                                write_json_atomic(root / "activity.json", data)
                            accounts.append(data)
            summary = {"version": 1, "started_at": self.data["started_at"], "updated_at": stamp.isoformat(),
                       "accounts": len(accounts), "legacy_accounts": sum(not a.get("first_login_at") for a in accounts),
                       "periods": {}, "days": self.data["days"]}
            for label, start, end in (("today", day, day), ("yesterday", day - timedelta(days=1), day - timedelta(days=1)),
                                      ("7d", day - timedelta(days=6), day), ("30d", day - timedelta(days=29), day)):
                low, high = start.isoformat(), end.isoformat()
                rows = [r for d, r in self.data["days"].items() if low <= d <= high]
                summary["periods"][label] = {
                    "active": sum(any(low <= d <= high for d in a.get("days", [])) for a in accounts),
                    "new": sum(low <= str(a.get("first_login_at") or "")[:10] <= high for a in accounts),
                    "views": sum(sum(r["pages"].values()) for r in rows),
                    "errors": sum(r["errors"] for r in rows),
                }
            write_json_atomic(self.root / "activity.json", self.data)
            write_json_atomic(self.root / "activity_summary.json", summary)

    @asynccontextmanager
    async def lifespan(self, app):
        async def flush():
            try:
                await run_in_threadpool(self.flush)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, FileLockTimeout):
                logger.error("웹 활동 통계 저장 실패; 마지막 집계 시각을 확인하세요")

        async def periodic():
            while True:
                await asyncio.sleep(30)
                await flush()

        await flush()
        task = asyncio.create_task(periodic())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await flush()
