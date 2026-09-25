"""뉴스 긴급 경로와 분리된 비긴급 작업 실행기.

리서치 분석처럼 오래 걸리는 단일 호출이 뉴스 주기와 겹치면, 원격 추론이라도
동시 요청이 늘어 속도 제한(429)에 걸리기 쉽고 뉴스 전송이 늦어진다. 그래서
스레드 분리와 별개로, 뉴스 주기가 도는 동안에는 비긴급 LLM 작업의 *시작*을
보류시킨다(`urgent_phase` / `wait_for_urgent_idle`).
"""

import asyncio
import logging
import os
import threading
import weakref
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial, wraps
from queue import Empty, Queue
from typing import Any, Callable

from services.telegram_bot.core.config import NON_URGENT_DEFER_TIMEOUT_SECONDS, NON_URGENT_WORKER_COUNT

logger = logging.getLogger(__name__)

_stopping = threading.Event()
_executors = weakref.WeakSet()
_worker_threads = weakref.WeakSet()
_registry_lock = threading.Lock()


class WorkerStopping(BaseException):
    """종료 신호. 수집기의 일반적인 오류 재시도에 삼켜지지 않는다."""


def request_shutdown() -> None:
    _stopping.set()


def reset_shutdown() -> None:
    _stopping.clear()


def is_stopping() -> bool:
    return _stopping.is_set()


def check_shutdown() -> None:
    if is_stopping():
        raise WorkerStopping()


def _execute(item):
    future, func, args, kwargs = item
    if future.set_running_or_notify_cancel():
        try:
            check_shutdown()
            result = func(*args, **kwargs)
        except BaseException as error:
            future.set_exception(error)
        else:
            future.set_result(result)


def _work(queue, idle):
    while (item := queue.get()) is not None:
        _execute(item)
        del item  # 큰 수집 결과·호출 인자를 다음 작업까지 붙잡지 않는다.
        idle.release()


class ShutdownThreadPool(ThreadPoolExecutor):
    """종료 시 제한 시간만 기다리는 고정 크기 daemon 풀.

    asyncio.set_default_executor의 타입 계약 때문에 ThreadPoolExecutor를 상속하지만
    submit/shutdown은 직접 구현한다. 부모의 private worker·atexit join에는 등록하지
    않는다. 실행 중 작업은 죽이지 않고, 프로세스 종료 시에만 OS가 회수한다.
    """

    def __init__(self, max_workers=None, thread_name_prefix="worker"):
        cpu_count = getattr(os, "process_cpu_count", os.cpu_count)
        self._limit = max_workers if max_workers is not None else min(32, (cpu_count() or 1) + 4)
        if self._limit <= 0:
            raise ValueError("max_workers must be greater than 0")
        self._name = thread_name_prefix
        self._queue = Queue()
        self._threads = []
        self._lock = threading.Lock()
        self._idle = threading.Semaphore(0)
        self._closed = False
        with _registry_lock:
            _executors.add(self)

    def submit(self, fn, /, *args, **kwargs):
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot schedule new futures after shutdown")
            future = Future()
            self._queue.put((future, fn, args, kwargs))
            if not self._idle.acquire(blocking=False) and len(self._threads) < self._limit:
                thread = threading.Thread(
                    target=_work, args=(self._queue, self._idle), daemon=True,
                    name=f"{self._name}_{len(self._threads)}",
                )
                self._threads.append(thread)
                with _registry_lock:
                    _worker_threads.add(thread)
                thread.start()
            return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        with self._lock:
            self._closed = True
            if cancel_futures:
                while True:
                    try:
                        item = self._queue.get_nowait()
                    except Empty:
                        break
                    if item is not None:
                        item[0].cancel()
            for _ in self._threads:
                self._queue.put(None)
        if wait:
            for thread in self._threads:
                thread.join()


async def drain_workers(timeout: float = 45.0) -> None:
    """새 작업을 막고 진행 중 요청/원자적 저장에 최대 45초를 준다."""
    request_shutdown()
    with _registry_lock:
        executors = list(_executors)
        threads = list(_worker_threads)
    for executor in executors:
        executor.shutdown(wait=False, cancel_futures=True)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while any(thread.is_alive() for thread in threads) and loop.time() < deadline:
        await asyncio.sleep(min(0.05, max(0, deadline - loop.time())))
    remaining = sorted(thread.name for thread in threads if thread.is_alive())
    if remaining:
        logger.warning("종료 대기 %.1f초 초과: 진행 중 작업을 포기합니다: %s", timeout, ", ".join(remaining))


_NON_URGENT_EXECUTOR = ShutdownThreadPool(
    max_workers=NON_URGENT_WORKER_COUNT,
    thread_name_prefix="non-urgent",
)


async def run_non_urgent(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """공유 대기열에서 빈 전용 워커가 비긴급·블로킹 작업을 가져간다.

    워커별 대기열에 순번으로 넣으면 외부 API에서 멈춘 작업 뒤의 뉴스 분석도
    함께 멈춘다. 하나의 대기열을 써서 나머지 워커가 계속 처리하게 한다.
    """
    call = partial(func, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(_NON_URGENT_EXECUTOR, call)


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
