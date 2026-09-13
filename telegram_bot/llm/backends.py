"""Cloudflare Workers AI 백엔드와 재시도·회로 차단 래퍼.

번역·시황 분석·브리핑이 모두 이 백엔드를 쓴다. 서비스 클래스는 프롬프트 구성과
결과 검증만 담당하고 HTTP 호출은 전부 여기로 모은다.

로그·예외 문자열에는 API 토큰과 기사 원문을 절대 넣지 않는다. 외부 응답을
그대로 옮길 때도 길이를 자르고 토큰 문자열을 마스킹한다.
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

import requests

logger = logging.getLogger(__name__)

# 외부 응답을 로그에 옮길 때의 상한. 원문이 통째로 새어 나가지 않게 한다.
_MAX_ERROR_DETAIL_CHARS = 200
# Retry-After를 그대로 믿으면 뉴스 주기를 통째로 잡아먹으므로 상한을 둔다.
_MAX_RETRY_AFTER_SECONDS = 10.0
# 429(일반 제한)에 걸렸을 때 열어 두는 짧은 쿨다운.
_RATE_LIMIT_COOLDOWN_SECONDS = 30.0
# Cloudflare가 무료 할당량 소진을 알릴 때 쓰는 문구들. 오류 코드가 정책에 따라
# 바뀔 수 있어 문구 매칭을 함께 쓴다.
_QUOTA_KEYWORDS = (
    "neuron",
    "quota",
    "daily limit",
    "free tier",
    "usage limit",
    "exceeded your",
    "out of credit",
)


class LLMBackendError(RuntimeError):
    """LLM 백엔드 호출 실패. 래퍼가 재시도·회로 차단을 결정할 정보를 담는다."""

    def __init__(
        self,
        provider: str,
        reason: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        quota_exhausted: bool = False,
        fatal: bool = False,
        caller_fault: bool = False,
        retry_after: float | None = None,
        detail: str = "",
    ):
        self.provider = provider
        self.reason = reason
        self.status_code = status_code
        self.retryable = retryable
        self.quota_exhausted = quota_exhausted
        # 재시작 전까지 회로를 열어야 하는 오류(인증·요청 형식 등).
        self.fatal = fatal
        # 공급자가 아니라 요청이 원인인 오류(출력 예약 부족 등). 회로 차단의
        # 연속 실패로 세지 않는다 - 한 호출자의 예산 문제로 다른 기능까지
        # 멈추면 안 된다.
        self.caller_fault = caller_fault
        self.retry_after = retry_after
        self.detail = detail

        message = f"{provider} {reason}"
        if status_code is not None:
            message = f"{message} (HTTP {status_code})"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Cloudflare는 과금 단위(Neurons)를 응답에 직접 담아 준다. 추정하지 말고
    # 이 값을 그대로 남겨 일일 무료 할당량 소진 속도를 추적한다.
    neurons: float | None = None

    def log_fragment(self) -> str:
        if self.input_tokens is None and self.output_tokens is None:
            return "usage=unknown"
        fragment = (
            f"input_tokens={self.input_tokens if self.input_tokens is not None else '?'} "
            f"output_tokens={self.output_tokens if self.output_tokens is not None else '?'}"
        )
        if self.neurons is not None:
            fragment += f" neurons={self.neurons:.2f}"
        return fragment


class LLMBackend(Protocol):
    """채팅 완성 호출 인터페이스. 번역·시황 분석·브리핑이 공유한다."""

    name: str
    model: str

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: float | None = None,
    ) -> str: ...


def _truncate(text: str, limit: int = _MAX_ERROR_DETAIL_CHARS) -> str:
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + "…"


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _parse_retry_after(value: Any) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


class CloudflareWorkersAIBackend:
    """Cloudflare Workers AI의 OpenAI 호환 Chat Completions 호출."""

    name = "cloudflare"

    def __init__(
        self,
        *,
        account_id: str,
        api_token: str,
        model: str,
        base_url: str = "https://api.cloudflare.com/client/v4",
        timeout: int = 45,
        session: Any | None = None,
    ):
        if not account_id or not api_token:
            raise ValueError(
                "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required"
            )
        self._api_token = api_token
        self.model = model
        self._url = (
            f"{base_url.rstrip('/')}/accounts/{account_id}/ai/v1/chat/completions"
        )
        self._timeout = timeout
        self._session = session if session is not None else requests.Session()
        # Qwen3는 추론 모델인데 이 엔드포인트에는 thinking을 끄는 옵션이 없다.
        # 그대로 두면 thinking이 max_tokens를 전부 먹고 finish_reason=length로
        # content가 잘려 나온다(실측: reasoning 3,510자 / content 14자, 34
        # Neurons). `/no_think` 지시어를 붙이면 정상 JSON이 오고 8 Neurons로
        # 떨어진다. chat_template_kwargs의 enable_thinking=false는 content를
        # 아예 비우므로 대안이 되지 못한다.
        self._suppress_thinking = "qwen3" in model.lower()

    @property
    def url(self) -> str:
        return self._url

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: float | None = None,
    ) -> str:
        started = time.monotonic()
        if self._suppress_thinking:
            system_prompt = f"{system_prompt}\n/no_think"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }
        try:
            response = self._session.post(
                self._url,
                json=payload,
                headers=headers,
                timeout=self._timeout if timeout is None else timeout,
            )
        except requests.Timeout as error:
            raise self._fail(started, "timeout", retryable=True, detail=str(error))
        except requests.RequestException as error:
            raise self._fail(started, "connection", retryable=True, detail=str(error))

        status = getattr(response, "status_code", None)
        if status is not None and status >= 400:
            raise self._http_failure(started, response, status)

        try:
            data = response.json()
        except ValueError as error:
            raise self._fail(
                started,
                "invalid_response",
                status_code=status,
                retryable=True,
                detail=f"response is not JSON: {error}",
            )

        content = self._extract_content(data)
        if content is None:
            raise self._fail(
                started,
                "invalid_response",
                status_code=status,
                retryable=True,
                detail=(
                    "response has no usable choices[0].message.content "
                    f"({self._empty_content_hint(data)})"
                ),
            )

        usage = self._usage(data)
        if self._finish_reason(data) == "length":
            # 상한에 걸려 끊긴 응답은 성공이 아니다. 그대로 돌려주면 호출자는
            # 파싱 오류(`Unterminated string`)만 보고 원인을 못 찾는다
            # (2026-08-15·2026-08-24 리서치 장애가 둘 다 이랬다). 재시도하지
            # 않는다 - 같은 입력은 같은 길이에서 다시 끊기고 Neurons만 두 배로
            # 태운다. 고칠 곳은 출력을 채우는 내용이거나 max_tokens 예약이다.
            raise self._fail(
                started,
                "truncated",
                status_code=status,
                retryable=False,
                caller_fault=True,
                detail=(
                    f"output stopped at max_tokens={max_tokens} "
                    f"({usage.log_fragment()})"
                ),
            )

        logger.info(
            "[LLM] provider=%s model=%s result=success latency_ms=%d %s",
            self.name,
            self.model,
            _elapsed_ms(started),
            usage.log_fragment(),
        )
        return content

    @staticmethod
    def _extract_content(data: Any) -> str | None:
        if not isinstance(data, dict):
            return None
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            return None
        return content

    @staticmethod
    def _finish_reason(data: Any) -> str | None:
        """choices[0].finish_reason. 주지 않는 응답은 None이다.

        값이 없다고 절단으로 단정하지 않는다 - 정상 완료를 실패로 바꾼다.
        """
        choices = data.get("choices") if isinstance(data, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else None
        reason = first.get("finish_reason") if isinstance(first, dict) else None
        return reason if isinstance(reason, str) else None

    @staticmethod
    def _empty_content_hint(data: Any) -> str:
        """content가 빈 이유를 진단할 짧은 힌트. 원문은 담지 않는다."""
        choices = (data or {}).get("choices") if isinstance(data, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else {}
        if not isinstance(first, dict):
            return "unexpected choice shape"
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        finish_reason = first.get("finish_reason")
        parts = [f"finish_reason={finish_reason}"]
        if isinstance(reasoning, str) and reasoning.strip():
            # 추론 모델이 thinking에만 답을 쓰고 끝난 경우. max_tokens를 올리거나
            # thinking을 억제해야 한다.
            parts.append(f"reasoning_chars={len(reasoning)}")
        return ", ".join(parts)

    @staticmethod
    def _usage(data: Any) -> TokenUsage:
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(usage, dict):
            return TokenUsage()
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        neurons = usage.get("neurons")
        return TokenUsage(
            input_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
            output_tokens=(
                completion_tokens if isinstance(completion_tokens, int) else None
            ),
            neurons=float(neurons) if isinstance(neurons, (int, float)) else None,
        )

    def _http_failure(
        self, started: float, response: Any, status: int
    ) -> LLMBackendError:
        detail = self._error_detail(response)
        lowered = detail.lower()
        quota = status == 402 or any(word in lowered for word in _QUOTA_KEYWORDS)
        retry_after = _parse_retry_after(
            (getattr(response, "headers", None) or {}).get("Retry-After")
        )

        if quota:
            reason, retryable, fatal = "quota_exhausted", False, False
        elif status in (401, 403):
            reason, retryable, fatal = "auth_error", False, True
        elif status == 429:
            reason, retryable, fatal = "rate_limited", True, False
        elif status >= 500:
            reason, retryable, fatal = "server_error", True, False
        else:
            # 400/404/422 등 요청 형식 오류는 재시도해도 같은 결과다.
            reason, retryable, fatal = "bad_request", False, True

        return self._fail(
            started,
            reason,
            status_code=status,
            retryable=retryable,
            quota_exhausted=quota,
            fatal=fatal,
            retry_after=retry_after,
            detail=detail,
        )

    def _error_detail(self, response: Any) -> str:
        """오류 응답에서 짧은 사유 문자열만 뽑는다."""
        try:
            data = response.json()
        except (ValueError, AttributeError):
            return self._redact(_truncate(getattr(response, "text", "") or ""))

        messages: list[str] = []
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                messages.append(str(error.get("message") or ""))
            elif isinstance(error, str):
                messages.append(error)
            errors = data.get("errors")
            if isinstance(errors, list):
                for item in errors:
                    if isinstance(item, dict):
                        code = item.get("code")
                        text = str(item.get("message") or "")
                        messages.append(f"[{code}] {text}" if code else text)
                    elif isinstance(item, str):
                        messages.append(item)
        joined = "; ".join(part for part in messages if part.strip())
        if not joined:
            joined = _truncate(json.dumps(data, ensure_ascii=False))
        return self._redact(_truncate(joined))

    def _redact(self, text: str) -> str:
        """응답이 토큰을 되돌려주더라도 로그·예외에는 남기지 않는다."""
        if self._api_token and self._api_token in text:
            return text.replace(self._api_token, "***")
        return text

    def _fail(
        self,
        started: float,
        reason: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        quota_exhausted: bool = False,
        fatal: bool = False,
        caller_fault: bool = False,
        retry_after: float | None = None,
        detail: str = "",
    ) -> LLMBackendError:
        safe_detail = self._redact(_truncate(detail))
        logger.warning(
            "[LLM] provider=%s model=%s result=%s status=%s latency_ms=%d detail=%s",
            self.name,
            self.model,
            reason,
            status_code if status_code is not None else "-",
            _elapsed_ms(started),
            safe_detail or "-",
        )
        return LLMBackendError(
            self.name,
            reason,
            status_code=status_code,
            retryable=retryable,
            quota_exhausted=quota_exhausted,
            fatal=fatal,
            caller_fault=caller_fault,
            retry_after=retry_after,
            detail=safe_detail,
        )


def _next_utc_midnight(now: float) -> float:
    """무료 할당량이 초기화되는 다음 UTC 00시의 epoch 초."""
    current = datetime.fromtimestamp(now, tz=timezone.utc)
    midnight = (current + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight.timestamp()


def _format_instant(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class ResilientBackend:
    """재시도와 회로 차단을 얹은 백엔드 래퍼.

    폴백 공급자는 없다. 무료 할당량이 소진되면 다음 UTC 00시까지, 인증·요청
    형식 오류는 재시작 전까지 호출을 멈추고 즉시 실패시킨다. 소진된 뒤에도
    계속 두드리면 요청마다 무의미한 왕복만 쌓이기 때문이다.

    회로 상태는 프로세스 메모리에만 둔다. 영속화하지 않는다.
    """

    def __init__(
        self,
        *,
        backend: LLMBackend,
        max_attempts: int = 2,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._backend = backend
        self._max_attempts = max(1, max_attempts)
        self._failure_threshold = max(1, failure_threshold)
        self._cooldown_seconds = max(1.0, float(cooldown_seconds))
        self._clock = clock
        self._sleep = sleep
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._open_permanently = False
        self._open_reason = ""

    @property
    def name(self) -> str:
        return self._backend.name

    @property
    def model(self) -> str:
        return self._backend.model

    def circuit_status(self) -> str:
        if self._open_permanently:
            return f"open(permanent,{self._open_reason})"
        if self._clock() < self._open_until:
            return f"open({self._open_reason},until={_format_instant(self._open_until)})"
        return "closed"

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: float | None = None,
    ) -> str:
        if self._circuit_open():
            logger.info(
                "[LLM] provider=%s result=skipped circuit=%s",
                self._backend.name,
                self.circuit_status(),
            )
            raise LLMBackendError(
                self._backend.name,
                "circuit_open",
                detail=f"circuit is {self.circuit_status()}",
            )

        kwargs = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout": timeout,
        }
        last_error: LLMBackendError | None = None
        for attempt in range(self._max_attempts):
            try:
                content = self._backend.generate(**kwargs)
            except LLMBackendError as error:
                last_error = error
                self._record_failure(error)
                if not error.retryable or attempt == self._max_attempts - 1:
                    break
                delay = min(error.retry_after or 0.0, _MAX_RETRY_AFTER_SECONDS)
                if delay > 0:
                    self._sleep(delay)
                continue
            self._record_success()
            return content

        assert last_error is not None
        raise last_error

    def _circuit_open(self) -> bool:
        if self._open_permanently:
            return True
        return self._clock() < self._open_until

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._open_reason = ""

    def _record_failure(self, error: LLMBackendError) -> None:
        if error.caller_fault:
            # 공급자는 정상 응답했다. 요청이 예약한 출력 한도가 모자란 것이라
            # 연속 실패로 세면 리서치 하나 때문에 뉴스 번역까지 멈춘다.
            return

        if error.quota_exhausted:
            self._open_until = _next_utc_midnight(self._clock())
            self._open_reason = "quota_exhausted"
            logger.warning(
                "[LLM] provider=%s result=quota_exhausted circuit_open_until=%s",
                self._backend.name,
                _format_instant(self._open_until),
            )
            return

        if error.fatal:
            self._open_permanently = True
            self._open_reason = error.reason
            logger.error(
                "[LLM] provider=%s result=%s circuit_open_until=restart",
                self._backend.name,
                error.reason,
            )
            return

        self._consecutive_failures += 1
        if error.status_code == 429:
            # 일반 속도 제한은 임계치와 무관하게 짧게 쉬었다 다시 시도한다.
            cooldown = min(
                self._cooldown_seconds,
                max(error.retry_after or 0.0, _RATE_LIMIT_COOLDOWN_SECONDS),
            )
        elif self._consecutive_failures >= self._failure_threshold:
            cooldown = self._cooldown_seconds
        else:
            return

        self._open_until = self._clock() + cooldown
        self._open_reason = error.reason
        logger.warning(
            "[LLM] provider=%s result=%s consecutive_failures=%d circuit_open_until=%s",
            self._backend.name,
            error.reason,
            self._consecutive_failures,
            _format_instant(self._open_until),
        )
