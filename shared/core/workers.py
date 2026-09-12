"""뉴스 긴급 경로와 분리된 비긴급 작업 실행기.

리서치 분석처럼 오래 걸리는 단일 호출이 뉴스 주기와 겹치면, 원격 추론이라도
동시 요청이 늘어 속도 제한(429)에 걸리기 쉽고 뉴스 전송이 늦어진다. 그래서
스레드 분리와 별개로, 뉴스 주기가 도는 동안에는 비긴급 LLM 작업의 *시작*을
보류시킨다(`urgent_phase` / `wait_for_urgent_idle`).
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial, wraps
from itertools import count
from threading import Lock
from typing import Any, Callable

from shared.core.config import NON_URGENT_DEFER_TIMEOUT_SECONDS, NON_URGENT_WORKER_COUNT

logger = logging.getLogger(__name__)

_NON_URGENT_EXECUTORS = tuple(
    ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix=f"non-urgent-{index + 1}",
    )
    for index in range(NON_URGENT_WORKER_COUNT)
)
_NON_URGENT_EXECUTOR_SEQUENCE = count()
_NON_URGENT_EXECUTOR_LOCK = Lock()


def _next_non_urgent_executor() -> ThreadPoolExecutor:
    """1→N 순서로 각 단일 스레드 실행기에 작업을 배정한다."""
    with _NON_URGENT_EXECUTOR_LOCK:
        index = next(_NON_URGENT_EXECUTOR_SEQUENCE) % len(_NON_URGENT_EXECUTORS)
    return _NON_URGENT_EXECUTORS[index]


async def run_non_urgent(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """비긴급·블로킹 작업을 라운드 로빈 전용 워커에서 실행한다."""
    call = partial(func, *args, **kwargs)
    executor = _next_non_urgent_executor()
    return await asyncio.get_running_loop().run_in_executor(executor, call)


# 고가치 분석은 평시 9% 예산을 넘겨 버스트 크레딧을 쓸 수 있다. 이 상태는
# 프리필터 유지보수 같은 양보 가능한 CPU 작업을 멈추는 신호이기도 하다.
_burst_depth = 0


def is_burst_active() -> bool:
    return _burst_depth > 0


@asynccontextmanager
async def burst_phase(label: str):
    """리서치·다이제스트·컨센서스가 버스트 자원을 우선 쓰는 구간."""
    global _burst_depth
    _burst_depth += 1
    logger.info("[WORKERS] 버스트 우선 구간 시작 - %s", label)
    try:
        yield
    finally:
        _burst_depth = max(0, _burst_depth - 1)
        logger.info("[WORKERS] 버스트 우선 구간 종료 - %s", label)


def burst_job(label: str):
    """async 작업 전체를 ``burst_phase``로 감싸는 데코레이터."""

    def decorate(func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            async with burst_phase(label):
                return await func(*args, **kwargs)

        return wrapped

    return decorate


# ── 긴급 구간 게이트 ──────────────────────────────────
# 봇과 같은 이벤트 루프에서만 쓰이므로 별도 락 없이 카운터로 중첩을 센다.

_urgent_depth = 0
_urgent_idle = asyncio.Event()
_urgent_idle.set()


@asynccontextmanager
async def urgent_phase():
    """긴급 구간(뉴스 수집·번역 주기)을 표시한다. 중첩 호출을 허용한다."""
    global _urgent_depth
    _urgent_depth += 1
    _urgent_idle.clear()
    try:
        yield
    finally:
        _urgent_depth = max(0, _urgent_depth - 1)
        if _urgent_depth == 0:
            _urgent_idle.set()


async def wait_for_urgent_idle(
    label: str,
    timeout: float | None = None,
) -> bool:
    """긴급 구간이 끝날 때까지 기다린다.

    LLM을 쓰는 비긴급 작업이 호출한다. 대기가 한도를 넘으면 굶기지 않도록
    그대로 진행한다(True=유휴 확인, False=한도 초과 후 강행).
    """
    if _urgent_idle.is_set():
        return True

    limit = NON_URGENT_DEFER_TIMEOUT_SECONDS if timeout is None else timeout
    if limit <= 0:
        return False

    logger.info("[WORKERS] 뉴스 주기 진행 중 - %s 보류 (최대 %.0f초)", label, limit)
    try:
        await asyncio.wait_for(_urgent_idle.wait(), timeout=limit)
    except asyncio.TimeoutError:
        logger.warning("[WORKERS] %s 보류 한도 초과, 그대로 진행합니다.", label)
        return False
    logger.info("[WORKERS] 뉴스 주기 종료 - %s 재개", label)
    return True
