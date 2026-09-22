"""에이전트 지침 포인터가 서로 갈라지지 않는다.

Claude Code는 `CLAUDE.md`를, Codex·Cursor 계열은 `AGENTS.md`를 자동으로 읽는다.
그래서 포인터가 둘이다. 규칙을 각 파일에 옮겨 적으면 기준이 다시 여러 벌이
되는데, 그걸 없애려고 `code_guide.md`로 합친 것이므로 포인터는 **내용이
완전히 같아야** 한다.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STANDARD = ROOT / "code_guide.md"
POINTERS = (
    ROOT / "CLAUDE.md",
    ROOT / "AGENTS.md",
)


def test_every_agent_pointer_exists_and_is_identical():
    missing = [p.name for p in POINTERS if not p.exists()]
    assert missing == [], f"포인터 누락: {missing}"

    texts = {p: p.read_text(encoding="utf-8") for p in POINTERS}
    first = texts[POINTERS[0]]
    drifted = [p.name for p, t in texts.items() if t != first]
    assert drifted == [], f"포인터 내용이 갈라졌다: {drifted}"


def test_every_pointer_sends_the_reader_to_the_standard():
    for path in POINTERS:
        assert "code_guide.md" in path.read_text(encoding="utf-8"), path.name


def test_pointers_stay_thin():
    """포인터가 길어지면 규칙이 옮겨 적히고 있다는 뜻이다."""
    for path in POINTERS:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines < 40, f"{path.name}이 {lines}줄이다. 규칙은 code_guide.md에 둔다"


def test_pointer_and_standard_agree_on_the_lint_target():
    r"""포인터와 기준 문서에 두 벌로 적힌 유일한 것이 lint 대상 목록이다.

    포인터는 실행 명령만 담고 규칙은 `code_guide.md`에 둔다는 것이 이 저장소의
    구조인데, 그 실행 명령 자체는 양쪽에 적혀 있다. 파이썬 경로가 달라
    (리눅스 `python` 대 PowerShell `.\venv\Scripts\python.exe`) 줄 전체는
    비교할 수 없으므로, 갈라지면 실제로 문제가 되는 **검사 대상 목록**을 맞춘다.
    한쪽에만 디렉터리를 추가하면 그쪽 지시만 따른 에이전트가 검사를 빠뜨린다.
    """
    pattern = re.compile(r"ruff check\s+(.+)")

    targets = {}
    for path in (*POINTERS, STANDARD):
        found = pattern.findall(path.read_text(encoding="utf-8"))
        assert found, f"{path.name}에 ruff check 명령이 없다"
        targets[path.name] = {line.strip() for line in found}

    assert len(set(map(frozenset, targets.values()))) == 1, (
        f"lint 대상이 갈라졌다: {targets}"
    )


def test_pointers_only_name_paths_that_exist():
    """포인터가 대는 저장소 경로는 실재해야 한다.

    포인터는 얇아서 파일을 몇 개만 지목하는데, 그 파일이 옮겨져도 포인터는
    조용히 남는다. 첫 칸이 최상위 디렉터리인 것만 본다 — `core/clock.py`처럼
    "그 모듈의"를 뜻하는 상대 표기는 저장소 경로가 아니다.
    """
    top_level = {child.name for child in ROOT.iterdir() if child.is_dir()}

    for path in POINTERS:
        cited = re.findall(r"`([A-Za-z_][\w/.-]*\.(?:py|md|txt))`", path.read_text(encoding="utf-8"))
        for ref in cited:
            if "/" not in ref or ref.split("/")[0] not in top_level:
                continue
            assert (ROOT / ref).exists(), f"{path.name}이 없는 경로를 가리킨다: {ref}"


def test_the_standard_itself_is_not_a_pointer():
    body = STANDARD.read_text(encoding="utf-8")
    assert len(body.splitlines()) > 200
    assert "## 기능 경계" in body
