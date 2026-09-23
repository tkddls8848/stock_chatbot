"""Cloudflare Workers AI 백엔드와 재시도·회로 차단 회귀 테스트.

실제 네트워크는 쓰지 않는다. 가짜 세션을 주입해 요청 구성, 오류 분류,
회로 차단을 검증한다.
"""

import json
import logging
from datetime import datetime, timezone

import pytest
import requests

from services.telegram_bot.llm.backends import (
    CloudflareWorkersAIBackend,
    LLMBackendError,
    ResilientBackend,
)
from services.telegram_bot.llm.translator import (
    TranslationError,
    TranslationQualityError,
    TranslationService,
)

# 번역 품질 게이트의 하한(60자·한글 비율)을 넘는 정상 응답 본문.
_KOREAN_BODY = (
    "귀주모태는 3분기 매출이 전년 대비 15% 늘어난 400억 위안이라고 발표했다. "
    "회사는 주력 제품의 출고가 인상이 실적을 끌어올렸다고 설명했다."
)

API_TOKEN = "cf-secret-token-should-never-be-logged"
GENERATE_KWARGS = {
    "system_prompt": "system",
    "user_prompt": "user",
    "max_tokens": 512,
    "temperature": 0.1,
}


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class _FakeSession:
    """미리 준비한 응답(또는 예외)을 순서대로 돌려주는 requests.Session 대역."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        item = self._responses.pop(0) if self._responses else _FakeResponse()
        if isinstance(item, Exception):
            raise item
        return item


def _chat_completion(content, usage=None):
    payload = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        payload["usage"] = usage
    return _FakeResponse(payload=payload)


def _cloudflare(session, **overrides):
    kwargs = {
        "account_id": "acct-1",
        "api_token": API_TOKEN,
        "model": "@cf/qwen/qwen3-30b-a3b-fp8",
        "base_url": "https://api.cloudflare.com/client/v4",
        "timeout": 45,
        "session": session,
    }
    kwargs.update(overrides)
    return CloudflareWorkersAIBackend(**kwargs)


# ── 요청 구성 ─────────────────────────────────────────


def test_request_uses_openai_compatible_endpoint():
    session = _FakeSession(_chat_completion('{"title": "제목"}'))

    content = _cloudflare(session).generate(**GENERATE_KWARGS)

    assert content == '{"title": "제목"}'
    call = session.calls[0]
    assert call["url"] == (
        "https://api.cloudflare.com/client/v4/accounts/acct-1/ai/v1/chat/completions"
    )
    assert call["headers"]["Authorization"] == f"Bearer {API_TOKEN}"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["timeout"] == 45
    payload = call["json"]
    assert payload["model"] == "@cf/qwen/qwen3-30b-a3b-fp8"
    assert payload["stream"] is False
    assert payload["temperature"] == 0.1
    assert payload["max_tokens"] == 512
    # Qwen3 기본 모델이라 시스템 프롬프트에 /no_think가 덧붙는다.
    assert payload["messages"] == [
        {"role": "system", "content": "system\n/no_think"},
        {"role": "user", "content": "user"},
    ]


def test_response_format_is_absent_unless_asked():
    """구조화 출력을 쓰지 않는 호출의 요청 본문은 전과 같아야 한다.

    이 모델이 `response_format`을 실제로 받는지 아직 재지 않았다. 키를 항상
    실으면 재기 전에 모든 호출 경로가 그 답에 걸린다.
    """
    session = _FakeSession(_chat_completion("{}"))

    _cloudflare(session).generate(**GENERATE_KWARGS)

    assert "response_format" not in session.calls[0]["json"]


def test_response_format_rides_the_request_when_given():
    session = _FakeSession(_chat_completion("{}"))
    schema = {"type": "json_schema", "json_schema": {"type": "object"}}

    _cloudflare(session).generate(**GENERATE_KWARGS, response_format=schema)

    assert session.calls[0]["json"]["response_format"] == schema


def test_per_request_timeout_overrides_default():
    session = _FakeSession(_chat_completion("{}"))

    _cloudflare(session).generate(**GENERATE_KWARGS, timeout=600)

    assert session.calls[0]["timeout"] == 600


def test_suppresses_qwen3_thinking():
    """Qwen3는 thinking이 max_tokens를 다 먹고 content를 비운다. /no_think로 막는다."""
    session = _FakeSession(_chat_completion("{}"))

    _cloudflare(session, model="@cf/qwen/qwen3-30b-a3b-fp8").generate(**GENERATE_KWARGS)

    assert session.calls[0]["json"]["messages"][0]["content"] == "system\n/no_think"
    assert session.calls[0]["json"]["messages"][1]["content"] == "user"


def test_leaves_non_qwen3_prompts_untouched():
    session = _FakeSession(_chat_completion("{}"))

    _cloudflare(session, model="@cf/meta/llama-3.1-8b-instruct").generate(
        **GENERATE_KWARGS
    )

    assert session.calls[0]["json"]["messages"][0]["content"] == "system"


def test_requires_credentials():
    with pytest.raises(ValueError):
        CloudflareWorkersAIBackend(account_id="", api_token="", model="m")


# ── 사용량 로깅 ───────────────────────────────────────


def test_logs_usage_with_neurons(caplog):
    session = _FakeSession(
        _chat_completion(
            "{}",
            usage={
                "prompt_tokens": 710,
                "completion_tokens": 196,
                "neurons": 9.2755,
            },
        )
    )
    with caplog.at_level(logging.INFO, logger="services.telegram_bot.llm.backends"):
        _cloudflare(session).generate(**GENERATE_KWARGS)

    assert "input_tokens=710" in caplog.text
    assert "output_tokens=196" in caplog.text
    # 과금 단위는 추정하지 않고 응답이 준 값을 그대로 남긴다.
    assert "neurons=9.28" in caplog.text


def test_logs_unknown_usage_when_absent(caplog):
    session = _FakeSession(_chat_completion("{}"))
    with caplog.at_level(logging.INFO, logger="services.telegram_bot.llm.backends"):
        _cloudflare(session).generate(**GENERATE_KWARGS)

    assert "usage=unknown" in caplog.text


# ── 응답 검증 ─────────────────────────────────────────


@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(payload={"choices": []}),
        _FakeResponse(payload={"choices": [{"message": {"content": "  "}}]}),
        _FakeResponse(payload={"result": "unexpected"}),
        _FakeResponse(payload=None, text="<html>gateway</html>"),
    ],
    ids=["empty-choices", "blank-content", "no-choices", "not-json"],
)
def test_rejects_unusable_responses(response):
    with pytest.raises(LLMBackendError) as excinfo:
        _cloudflare(_FakeSession(response)).generate(**GENERATE_KWARGS)

    error = excinfo.value
    assert error.provider == "cloudflare"
    assert error.reason == "invalid_response"
    assert error.retryable is True


def test_reports_reasoning_only_response():
    """thinking에만 답을 쓰고 끝난 응답은 원인이 로그에서 보여야 한다."""
    session = _FakeSession(
        _FakeResponse(
            payload={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "", "reasoning_content": "x" * 3510},
                    }
                ]
            }
        )
    )

    with pytest.raises(LLMBackendError) as excinfo:
        _cloudflare(session).generate(**GENERATE_KWARGS)

    detail = excinfo.value.detail
    assert "finish_reason=length" in detail
    assert "reasoning_chars=3510" in detail


# ── 오류 분류 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "reason", "retryable", "fatal"),
    [
        (429, "rate_limited", True, False),
        (500, "server_error", True, False),
        (503, "server_error", True, False),
        (401, "auth_error", False, True),
        (403, "auth_error", False, True),
        (400, "bad_request", False, True),
        (422, "bad_request", False, True),
    ],
)
def test_classifies_http_status(status, reason, retryable, fatal):
    session = _FakeSession(
        _FakeResponse(
            status_code=status,
            payload={"errors": [{"code": 7003, "message": "request failed"}]},
        )
    )

    with pytest.raises(LLMBackendError) as excinfo:
        _cloudflare(session).generate(**GENERATE_KWARGS)

    error = excinfo.value
    assert (error.reason, error.retryable, error.fatal) == (reason, retryable, fatal)
    assert error.status_code == status
    assert error.quota_exhausted is False


@pytest.mark.parametrize(
    "error",
    [requests.Timeout("read timed out"), requests.ConnectionError("refused")],
    ids=["timeout", "connection"],
)
def test_transport_errors_are_retryable(error):
    with pytest.raises(LLMBackendError) as excinfo:
        _cloudflare(_FakeSession(error)).generate(**GENERATE_KWARGS)

    assert excinfo.value.retryable is True
    assert excinfo.value.quota_exhausted is False


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (429, "You have exceeded your daily free tier limit of 10000 Neurons"),
        (402, "Payment required"),
    ],
    ids=["neuron-quota", "payment-required"],
)
def test_detects_quota_exhaustion(status, message):
    session = _FakeSession(
        _FakeResponse(status_code=status, payload={"error": {"message": message}})
    )

    with pytest.raises(LLMBackendError) as excinfo:
        _cloudflare(session).generate(**GENERATE_KWARGS)

    assert excinfo.value.quota_exhausted is True
    assert excinfo.value.retryable is False


def test_never_leaks_api_token(caplog):
    session = _FakeSession(
        _FakeResponse(
            status_code=403,
            payload={
                "errors": [
                    {
                        "code": 10000,
                        "message": f"Invalid token {API_TOKEN} for this account",
                    }
                ]
            },
        )
    )
    with caplog.at_level(logging.DEBUG, logger="services.telegram_bot.llm.backends"):
        with pytest.raises(LLMBackendError) as excinfo:
            _cloudflare(session).generate(**GENERATE_KWARGS)

    assert API_TOKEN not in str(excinfo.value)
    assert API_TOKEN not in caplog.text
    assert "***" in str(excinfo.value)


# ── 재시도와 회로 차단 ────────────────────────────────


class _Clock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _resilient(session, clock=None, **overrides):
    clock = clock or _Clock()
    kwargs = {
        "backend": _cloudflare(session),
        "max_attempts": 2,
        "failure_threshold": 3,
        "cooldown_seconds": 300,
        "clock": clock,
        "sleep": lambda _seconds: None,
    }
    kwargs.update(overrides)
    return ResilientBackend(**kwargs), clock


def test_passes_through_on_success():
    session = _FakeSession(_chat_completion("ok"))
    backend, _ = _resilient(session)

    assert backend.generate(**GENERATE_KWARGS) == "ok"
    assert len(session.calls) == 1
    assert backend.circuit_status() == "closed"


def test_response_format_survives_the_resilient_wrapper():
    """회로 차단·재시도 래퍼가 파라미터를 떨어뜨리면 구조화 출력이 조용히 꺼진다."""
    session = _FakeSession(_chat_completion("{}"))
    backend, _ = _resilient(session)
    schema = {"type": "json_schema", "json_schema": {"type": "object"}}

    backend.generate(**GENERATE_KWARGS, response_format=schema)

    assert session.calls[0]["json"]["response_format"] == schema


def test_retries_retryable_errors_then_succeeds():
    session = _FakeSession(
        _FakeResponse(status_code=503, payload={"errors": [{"message": "down"}]}),
        _chat_completion("recovered"),
    )
    backend, _ = _resilient(session)

    assert backend.generate(**GENERATE_KWARGS) == "recovered"
    assert len(session.calls) == 2


def test_honours_retry_after_within_cap():
    slept = []
    session = _FakeSession(
        _FakeResponse(
            status_code=429,
            payload={"error": {"message": "slow down"}},
            headers={"Retry-After": "2"},
        ),
        _chat_completion("after-retry"),
    )
    backend, _ = _resilient(session, sleep=slept.append)

    assert backend.generate(**GENERATE_KWARGS) == "after-retry"
    assert slept == [2.0]


def test_does_not_retry_non_retryable_errors():
    session = _FakeSession(
        _FakeResponse(status_code=400, payload={"error": {"message": "bad json"}}),
    )
    backend, _ = _resilient(session)

    with pytest.raises(LLMBackendError):
        backend.generate(**GENERATE_KWARGS)
    assert len(session.calls) == 1


def test_quota_exhaustion_opens_circuit_until_next_utc_midnight():
    noon = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    session = _FakeSession(
        _FakeResponse(
            status_code=429,
            payload={"error": {"message": "Daily free tier Neurons exhausted"}},
        )
    )
    backend, clock = _resilient(session, clock=_Clock(noon))

    with pytest.raises(LLMBackendError):
        backend.generate(**GENERATE_KWARGS)
    # 할당량 소진은 재시도하지 않는다.
    assert len(session.calls) == 1

    expected = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)
    assert "2026-08-02T00:00:00Z" in backend.circuit_status()
    assert "quota_exhausted" in backend.circuit_status()

    # 회로가 열린 동안에는 네트워크를 아예 건드리지 않고 즉시 실패한다.
    clock.advance(3600)
    with pytest.raises(LLMBackendError) as excinfo:
        backend.generate(**GENERATE_KWARGS)
    assert len(session.calls) == 1
    assert excinfo.value.reason == "circuit_open"

    # UTC 자정을 넘기면 다시 시도한다.
    clock.now = expected.timestamp() + 1
    assert backend.circuit_status() == "closed"


def test_auth_error_opens_circuit_until_restart():
    session = _FakeSession(
        _FakeResponse(status_code=401, payload={"error": {"message": "bad token"}})
    )
    backend, clock = _resilient(session)

    with pytest.raises(LLMBackendError):
        backend.generate(**GENERATE_KWARGS)
    assert "permanent" in backend.circuit_status()

    clock.advance(10 * 24 * 3600)
    with pytest.raises(LLMBackendError):
        backend.generate(**GENERATE_KWARGS)
    assert len(session.calls) == 1


def test_consecutive_failures_open_circuit_and_cooldown_expires():
    session = _FakeSession(
        *[requests.ConnectionError("x")] * 4,
        _chat_completion("back-online"),
    )
    backend, clock = _resilient(session, failure_threshold=3, max_attempts=2)

    # 호출 1회당 재시도 2회이므로 두 번째 호출에서 임계치 3에 도달한다.
    with pytest.raises(LLMBackendError):
        backend.generate(**GENERATE_KWARGS)
    assert backend.circuit_status() == "closed"
    with pytest.raises(LLMBackendError):
        backend.generate(**GENERATE_KWARGS)
    assert backend.circuit_status().startswith("open(")
    assert len(session.calls) == 4

    # 쿨다운 중에는 건너뛴다.
    with pytest.raises(LLMBackendError):
        backend.generate(**GENERATE_KWARGS)
    assert len(session.calls) == 4

    clock.advance(301)
    assert backend.generate(**GENERATE_KWARGS) == "back-online"
    assert len(session.calls) == 5
    assert backend.circuit_status() == "closed"


# ── TranslationService 통합 ───────────────────────────


def _service(backend):
    service = object.__new__(TranslationService)
    service._backend = backend
    service._enabled = True
    service._num_predict = 512
    service._temperature = 0.1
    service._prompt = "prompt"
    return service


def test_service_translates_through_the_backend():
    payload = json.dumps(
        {
            "title": "제목",
            "content": _KOREAN_BODY,
            "mentioned_stocks": ["600519"],
            "sentiment": 0.4,
            "impact": "medium",
        },
        ensure_ascii=False,
    )
    session = _FakeSession(_chat_completion(payload))
    backend, _ = _resilient(session)

    result = _service(backend).translate_article("原文", "原文 본문")

    assert result.title == "제목"
    assert result.mentioned_stocks == ["600519"]
    assert result.sentiment == 0.4
    system_prompt = session.calls[0]["json"]["messages"][0]["content"]
    assert system_prompt.startswith("prompt")
    assert system_prompt == "prompt\n/no_think"


def test_service_raises_translation_error_when_backend_fails():
    session = _FakeSession(requests.Timeout("t"), requests.Timeout("t"))
    backend, _ = _resilient(session)

    with pytest.raises(TranslationError, match="timeout"):
        _service(backend).translate_article("原文", "原文 본문")


def _quality_session(content, title="제목"):
    payload = json.dumps(
        {
            "title": title,
            "content": content,
            "mentioned_stocks": [],
            "sentiment": 0.1,
            "impact": "low",
        },
        ensure_ascii=False,
    )
    return _FakeSession(_chat_completion(payload))


def test_untranslated_body_is_rejected_as_a_quality_failure():
    """모델이 원문을 그대로 돌려주는 주기가 있다. 그대로 보내면 안 된다."""
    backend, _ = _resilient(
        _quality_session(
            "Kweichow Moutai said third-quarter revenue rose 15% from a year "
            "earlier to 40 billion yuan on higher shipment prices."
        )
    )

    with pytest.raises(TranslationQualityError):
        _service(backend).translate_article("原文", "原文 본문")


def test_title_echo_is_rejected_as_a_quality_failure():
    body = "귀주모태 3분기 매출 15% 증가, 출고가 인상이 실적을 끌어올렸다고 회사가 설명했다."
    backend, _ = _resilient(_quality_session(body, title=body))

    with pytest.raises(TranslationQualityError):
        _service(backend).translate_article("原文", "原文 본문")


def test_one_line_body_is_rejected_as_a_quality_failure():
    backend, _ = _resilient(_quality_session("귀주모태 실적 발표."))

    with pytest.raises(TranslationQualityError):
        _service(backend).translate_article("原文", "原文 본문")


def test_quality_failure_is_distinguishable_from_a_format_failure():
    """호출자가 재시도 대상에서 뺄 수 있도록 형식 오류와 타입이 갈린다."""
    assert issubclass(TranslationQualityError, TranslationError)


# ── 출력 상한 절단 감지 ────────────────────────────────


def _truncated_completion(content, usage=None):
    """max_tokens에 걸려 끊긴 응답. content는 있지만 완결되지 않았다."""
    payload = {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "length",
            }
        ]
    }
    if usage is not None:
        payload["usage"] = usage
    return _FakeResponse(payload=payload)


def test_truncated_response_raises_instead_of_returning_partial_json():
    """finish_reason=length는 성공이 아니다.

    그대로 돌려주면 호출자가 `Unterminated string`만 보고 원인을 못 찾는다
    (2026-08-15·2026-08-24 리서치 장애). 상한에 걸렸다고 여기서 말한다.
    """
    session = _FakeSession(
        _truncated_completion(
            '{"summary": "요약", "actions": [{"reason": "중간에서 끊',
            usage={"prompt_tokens": 20612, "completion_tokens": 512, "neurons": 344.97},
        )
    )

    with pytest.raises(LLMBackendError) as excinfo:
        _cloudflare(session).generate(**GENERATE_KWARGS)

    error = excinfo.value
    assert error.reason == "truncated"
    # 재시도해도 같은 길이에서 다시 끊긴다 - Neurons만 두 배로 태운다.
    assert error.retryable is False
    # 무엇을 줄이거나 올려야 하는지 한 줄로 보인다.
    assert "max_tokens=512" in error.detail
    assert "output_tokens=512" in error.detail


def test_truncated_response_does_not_open_circuit():
    """출력 예약이 모자란 것은 호출자 사정이라 provider 건강도로 세지 않는다.

    여기서 회로를 열면 리서치 하나 때문에 뉴스 번역까지 멈춘다.
    """
    session = _FakeSession(
        _truncated_completion("잘린 본문"),
        _truncated_completion("잘린 본문"),
        _chat_completion('{"ok": true}'),
    )
    backend = ResilientBackend(
        backend=_cloudflare(session),
        max_attempts=1,
        failure_threshold=2,
        cooldown_seconds=600,
    )

    for _ in range(2):
        with pytest.raises(LLMBackendError):
            backend.generate(**GENERATE_KWARGS)

    assert backend.circuit_status() == "closed"
    assert backend.generate(**GENERATE_KWARGS) == '{"ok": true}'


def test_finish_reason_stop_returns_content():
    session = _FakeSession(
        _FakeResponse(
            payload={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"ok": true}'},
                        "finish_reason": "stop",
                    }
                ]
            }
        )
    )

    assert _cloudflare(session).generate(**GENERATE_KWARGS) == '{"ok": true}'


def test_missing_finish_reason_is_not_treated_as_truncation():
    """finish_reason을 주지 않는 응답을 절단으로 단정하지 않는다."""
    session = _FakeSession(_chat_completion('{"ok": true}'))

    assert _cloudflare(session).generate(**GENERATE_KWARGS) == '{"ok": true}'
