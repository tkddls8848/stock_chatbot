"""웹이 부르는 모든 모델 프롬프트 앞에 경제·정치 용어 기준이 붙는다(운영자 지시 2026-10-02).

봇에도 같은 표가 있지만 별개 파일이다(모듈끼리 코드를 공유하지 않는다). 웹 쪽은
집단 예측 자료의 호칭 규칙(참여 규모·참여 잔액)을 함께 지켜야 한다.
"""

import ast
from pathlib import Path

from services.web.llm.terminology import TERMINOLOGY_FILE, read_prompt, with_terminology

WEB_DIR = Path(__file__).resolve().parents[1]


def test_the_guide_fixes_the_terms_and_keeps_the_forecast_naming_rule():
    guide = TERMINOLOGY_FILE.read_text(encoding="utf-8")
    assert "Treasury yields → 미 국채 금리" in guide
    # 예측 컨센서스 화면은 유동성·거래량 대신 참여 잔액·참여 규모다(code_guide).
    assert "참여 잔액" in guide and "실제 금융시장을 말할 때만" in guide


def test_read_prompt_puts_the_guide_first(tmp_path):
    prompt = tmp_path / "p.txt"
    prompt.write_text("본문만 출력한다.", encoding="utf-8")
    assert read_prompt(prompt).startswith("[용어 기준]")
    assert with_terminology("x").endswith("\n\nx")


def test_no_llm_caller_reads_a_prompt_file_directly():
    offenders = []
    for path in [*(WEB_DIR / "llm").glob("*.py"), WEB_DIR / "personal_research.py"]:
        if path.name == "terminology.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "read_text"
                    and "PROMPT" in ast.unparse(node.func.value).upper()):
                offenders.append(path.name)
    assert offenders == []


def test_personal_research_prompt_carries_the_guide():
    """개인 리서치는 운영자 리서치와 같은 프롬프트를 웹 사본으로 읽는다. 용어 기준이 앞에 붙는다."""
    from services.web.core import config
    from services.web.llm.market_view import MarketViewAnalyzer

    analyzer = MarketViewAnalyzer(backend=None, timeout=1, num_predict=1, prompt_file=config.RESEARCH_PROMPT_FILE)
    assert analyzer._prompt.startswith("[용어 기준]")
    assert "market_view" in analyzer._prompt and "view_critique" in analyzer._prompt


