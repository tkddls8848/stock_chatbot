"""Qwen3는 thinking이 출력 상한을 먹으므로 `/no_think`로 끈다."""

from dataclasses import replace

from polymarket_shorts import llm
from polymarket_shorts.config import Settings


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"finish_reason": "stop", "message": {"content": "{\"ok\": true}"}}]}


def _capture(monkeypatch):
    sent = {}

    def post(url, **kwargs):
        sent.update(kwargs["json"])
        return _Response()

    monkeypatch.setattr(llm.requests, "post", post)
    return sent


def _settings(model):
    return replace(Settings.from_env(), editor_account_id="a", editor_api_token="t", editor_model=model)


def test_qwen3_gets_no_think(monkeypatch):
    sent = _capture(monkeypatch)
    llm.chat_json(_settings("@cf/qwen/qwen3-30b-a3b-fp8"), system="sys", user="u", max_tokens=10)
    assert sent["messages"][0]["content"].endswith("\n/no_think")


def test_other_models_are_untouched(monkeypatch):
    sent = _capture(monkeypatch)
    llm.chat_json(_settings("@cf/meta/other"), system="sys", user="u", max_tokens=10)
    assert sent["messages"][0]["content"] == "sys"
