"""끊긴 응답은 원문 없이 모양(반복·thinking 누출)만 로그에 남긴다."""

from services.web.llm.backends import CloudflareWorkersAIBackend


def _data(reasoning=""):
    return {"choices": [{"finish_reason": "length", "message": {"content": "x", "reasoning_content": reasoning}}]}


def test_repetition_is_counted_without_copying_the_text():
    looped = "시장 흐름이 이어지고 있다. " * 40
    hint = CloudflareWorkersAIBackend._truncation_hint(_data(), looped)
    assert "max_sentence_repeat=40" in hint
    assert "시장" not in hint


def test_leaked_thinking_is_reported():
    hint = CloudflareWorkersAIBackend._truncation_hint(_data("길게 생각" * 10), "<think>생각 중")
    assert "reasoning_chars=50" in hint and "think_tag=True" in hint
