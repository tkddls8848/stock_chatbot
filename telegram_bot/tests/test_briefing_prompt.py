from shared.core.config import BRIEFING_PROMPT_FILE


def test_briefing_prompt_supports_all_automatic_session_kinds():
    prompt = BRIEFING_PROMPT_FILE.read_text(encoding="utf-8")

    assert "morning(장전)" in prompt
    assert "intraday(장중)" in prompt
    assert "evening(장후)" in prompt
    assert "남은 장에서 확인할 포인트" in prompt
