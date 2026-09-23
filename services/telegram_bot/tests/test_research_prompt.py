from pathlib import Path


PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "market_research_ko.txt"


def test_research_prompt_requires_diverse_but_grounded_hypotheses():
    text = PROMPT.read_text(encoding="utf-8")

    assert "2차·3차 파급" in text
    assert "기준 경로, 강화 경로, 반전 경로" in text
    assert "역발상" in text
    assert "입력에 없는 수치·사실을 지어내지 않는다" in text
    assert "정의하지 않은 새 출력 필드를 추가" in text


def test_research_prompt_keeps_existing_output_contract():
    text = PROMPT.read_text(encoding="utf-8")

    for field in ("summary", "actions", "risks", "view_critique"):
        assert f'"{field}"' in text
    for action in ("add", "remove", "watch"):
        assert action in text
