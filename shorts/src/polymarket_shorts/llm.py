"""Cloudflare Workers AI 채팅 호출 하나. JSON 객체만 받아 돌려준다.

`workflow`(검수 편집)와 `highlights`(이슈 선별)가 같은 엔드포인트를 쓴다.
POST를 두 벌 두면 한쪽의 envelope 수정이 다른 쪽에 가지 않아, 끊긴 응답 처리
같은 규칙이 조용히 갈라진다.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from .config import Settings


class LLMError(RuntimeError):
    pass


class TruncatedError(LLMError):
    """상한에 걸려 끊긴 응답. 재시도하지 않는다 — 같은 입력은 같은 자리에서 끊긴다."""


def chat_json(settings: Settings, *, system: str, user: str, max_tokens: int) -> dict[str, Any]:
    if not settings.editor_account_id or not settings.editor_api_token:
        raise LLMError("shorts/.env에 CLOUDFLARE_ACCOUNT_ID와 CLOUDFLARE_API_TOKEN을 설정하세요")
    # Qwen3는 추론 모델이라 thinking이 max_tokens를 먹고 finish_reason=length로 끊긴다
    # (서버 실측 2026-09-25: 이슈 선별이 max_tokens=2000에서 잘렸다). 봇과 같은
    # 방법으로 `/no_think` 지시어를 붙여 끈다 — 이 엔드포인트엔 끄는 옵션이 없다.
    if "qwen3" in settings.editor_model.lower():
        system = f"{system}\n/no_think"
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{settings.editor_account_id}"
        "/ai/v1/chat/completions"
    )
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {settings.editor_api_token}"},
            json={
                "model": settings.editor_model, "temperature": 0.2, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=(10, 120),
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        raise LLMError("API 호출 또는 응답 envelope 검증에 실패했습니다") from None
    if choice.get("finish_reason") != "stop":
        raise TruncatedError("응답이 상한에 걸려 끊겼습니다")
    try:
        payload = json.loads(choice["message"]["content"])
    except (ValueError, KeyError, TypeError):
        raise LLMError("응답 본문이 JSON이 아닙니다") from None
    if not isinstance(payload, dict):
        raise LLMError("응답 본문이 JSON 객체가 아닙니다")
    return payload
