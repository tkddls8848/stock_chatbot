"""Gamma JSON 읽기 전용 transport와 재시도 계측."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 10.0


class PolymarketError(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after: float = 0.0,
        detail: str = "",
    ):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after
        self.detail = detail[:500]


@dataclass
class RequestMetrics:
    request_count: int = 0
    retry_count: int = 0
    response_bytes: int = 0


class ProxyFallbackSession(requests.Session):
    """프록시 연결 자체가 실패할 때만 직접 연결로 한 번 전환한다."""

    def __init__(self, proxy_url: str):
        super().__init__()
        self.proxies = {"http": proxy_url, "https": proxy_url}
        self._direct = requests.Session()
        self._direct.trust_env = False
        self._proxy_available = True

    def request(self, method: str, url: str, **kwargs: Any):
        if self._proxy_available:
            try:
                return super().request(method, url, **kwargs)
            except (requests.exceptions.ProxyError, requests.Timeout):
                self._proxy_available = False
                logger.warning("[POLYMARKET_WEB] 프록시 연결 실패; 직접 연결로 전환합니다.")
        return self._direct.request(method, url, **kwargs)


def build_session(proxy_url: str) -> requests.Session | None:
    return ProxyFallbackSession(proxy_url) if proxy_url else None


def _retry_after(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


class JsonEndpoint:
    """기존 Polymarket client의 timeout·429·5xx 재시도 계약을 옮긴 구현."""

    def __init__(
        self,
        *,
        url: str,
        timeout: float,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.url = url
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()
        self.sleep = sleep
        self.metrics = RequestMetrics()

    def request(self, params: dict[str, Any]) -> Any:
        last_error: PolymarketError | None = None
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                self.metrics.retry_count += 1
            try:
                return self._request_once(params)
            except PolymarketError as error:
                last_error = error
                if not error.retryable or attempt == MAX_ATTEMPTS - 1:
                    break
                if error.retry_after:
                    self.sleep(min(error.retry_after, MAX_RETRY_AFTER_SECONDS))
        assert last_error is not None
        raise last_error

    def _request_once(self, params: dict[str, Any]) -> Any:
        self.metrics.request_count += 1
        try:
            response = self.session.get(self.url, params=params, timeout=self.timeout)
        except requests.Timeout as error:
            raise self._failure("timeout", retryable=True, detail=str(error)) from error
        except requests.RequestException as error:
            raise self._failure("connection", retryable=True, detail=str(error)) from error

        self.metrics.response_bytes += len(response.content)
        if response.status_code >= 400:
            if response.status_code == 429:
                reason, retryable = "rate_limited", True
            elif response.status_code >= 500:
                reason, retryable = "server_error", True
            else:
                reason, retryable = "bad_request", False
            raise self._failure(
                reason,
                status_code=response.status_code,
                retryable=retryable,
                retry_after=_retry_after(response.headers.get("Retry-After")),
                detail=response.text,
            )
        try:
            return response.json()
        except ValueError as error:
            raise self._failure(
                "invalid_response",
                status_code=response.status_code,
                detail=str(error),
            ) from error

    @staticmethod
    def _failure(
        reason: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after: float = 0.0,
        detail: str = "",
    ) -> PolymarketError:
        error = PolymarketError(
            reason,
            status_code=status_code,
            retryable=retryable,
            retry_after=retry_after,
            detail=detail,
        )
        logger.warning(
            "[POLYMARKET_WEB] result=%s status=%s detail=%s",
            reason,
            status_code if status_code is not None else "-",
            error.detail or "-",
        )
        return error
