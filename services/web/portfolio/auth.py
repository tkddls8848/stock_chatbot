"""개인 화면의 간단한 잠금.

비밀번호 하나(`PORTFOLIO_PASSWORD`)를 맞히면 쿠키를 준다. 쿠키 값은 비밀번호로 만든
HMAC이라 서버가 세션을 기억하지 않는다 — 프로세스를 재기동해도 풀리지 않고,
비밀번호를 바꾸면 기존 쿠키가 전부 풀린다. 목적은 한 사람이 쓰는 화면을 지나가는
사람에게서 닫는 것이지 계정 체계가 아니다(회원가입·2단계 인증·OAuth는 만들지 않는다).
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import deque

_SESSION_CONTEXT = b"nunchi-portfolio-session-v1"


def session_token(password: str) -> str:
    return hmac.new(password.encode("utf-8"), _SESSION_CONTEXT, hashlib.sha256).hexdigest()


def password_matches(candidate: str, password: str) -> bool:
    if not password:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), password.encode("utf-8"))


def session_valid(cookie: str | None, password: str) -> bool:
    if not password or not cookie:
        return False
    return hmac.compare_digest(cookie, session_token(password))


class LoginThrottle:
    """틀린 비밀번호를 창 안에서 센다. 넘으면 잠시 막는다(프로세스 메모리만)."""

    def __init__(self, max_failures: int, window_seconds: float, clock=time.monotonic):
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> deque[float]:
        stamps = self._failures.setdefault(key, deque())
        cutoff = self._clock() - self._window
        while stamps and stamps[0] < cutoff:
            stamps.popleft()
        return stamps

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key)) >= self._max

    def fail(self, key: str) -> None:
        with self._lock:
            self._recent(key).append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
