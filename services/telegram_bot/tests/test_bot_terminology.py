"""봇이 부르는 모든 모델 프롬프트 앞에 경제·정치 용어 기준이 붙는다(운영자 지시 2026-10-02).

"liquidity → 액화"처럼 낱말 직역을 막는 표다. 분석기 하나라도 이 함수를 거치지 않으면
그 경로의 글만 직역투로 남는다.
"""

import ast
from pathlib import Path

from services.telegram_bot.llm.terminology import TERMINOLOGY_FILE, read_prompt

LLM_DIR = Path(__file__).resolve().parents[1] / "llm"


def test_the_guide_fixes_the_terms_the_operator_named():
    guide = TERMINOLOGY_FILE.read_text(encoding="utf-8")
    assert "liquidity → 유동성" in guide
    assert "Treasury yields → 미 국채 금리" in guide
    assert "private payrolls(ADP) → 민간 고용" in guide


def test_read_prompt_puts_the_guide_first_so_the_output_format_stays_last(tmp_path):
    prompt = tmp_path / "p.txt"
    prompt.write_text("JSON만 출력한다.", encoding="utf-8")
    text = read_prompt(prompt)
    assert text.startswith("[용어 기준]") and text.endswith("JSON만 출력한다.")


def test_no_analyzer_reads_a_prompt_file_directly():
    """프롬프트 파일을 직접 `read_text`하면 용어 기준을 건너뛴다."""
    offenders = []
    for path in LLM_DIR.glob("*.py"):
        if path.name == "terminology.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "read_text":
                offenders.append(path.name)
    assert offenders == []
