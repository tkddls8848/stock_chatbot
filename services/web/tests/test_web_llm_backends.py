"""웹 Cloudflare 백엔드의 요청 변환을 mock 세션으로 검증한다."""

import json
from copy import deepcopy
from unittest.mock import Mock

from services.web.llm.backends import CloudflareWorkersAIBackend


def test_gemma4_sends_schema_in_prompt_only_for_json_schema():
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string", "description": "요약"}},
        "required": ["summary"],
        "additionalProperties": False,
    }
    formats = [
        {"type": "json_schema", "json_schema": {"name": "report", "schema": schema}},
        {"type": "json_schema", "json_schema": schema},
        {"type": "json_object"},
        {"type": "text"},
        None,
    ]
    for model in ("@cf/google/gemma-4-26b-a4b-it", "@cf/deepseek-ai/deepseek-v4.1-flash"):
        for response_format in formats:
            original = deepcopy(response_format)
            session = Mock()
            session.post.return_value.status_code = 200
            session.post.return_value.json.return_value = {
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]
            }
            backend = CloudflareWorkersAIBackend(
                account_id="a", api_token="t", model=model, session=session
            )
            assert backend.generate(
                system_prompt="system", user_prompt="user", max_tokens=512,
                temperature=0.1, response_format=response_format,
            ) == "{}"
            payload = session.post.call_args.kwargs["json"]
            prompt = payload["messages"][0]["content"]
            if "gemma-4" in model and response_format and response_format["type"] == "json_schema":
                assert payload["response_format"] == {"type": "json_object"}
                prefix = "system\n\n반드시 다음 JSON 형식으로만 답한다: "
                assert prompt.startswith(prefix)
                assert "summary" in prompt
                assert json.loads(prompt[len(prefix):]) == schema
            else:
                assert payload.get("response_format") == response_format
                assert prompt == "system"
                if response_format is None:
                    assert "response_format" not in payload
            assert response_format == original
            assert payload["messages"][1] == {"role": "user", "content": "user"}
            assert payload["chat_template_kwargs"] == {"thinking": False, "enable_thinking": False}
