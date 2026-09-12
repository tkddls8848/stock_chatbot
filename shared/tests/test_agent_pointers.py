"""에이전트 지침 포인터가 서로 갈라지지 않는다.

Claude Code는 `CLAUDE.md`를, Codex·Cursor 계열은 `AGENTS.md`를 자동으로 읽는다.
그래서 포인터가 둘이다. 규칙을 각 파일에 옮겨 적으면 기준이 다시 여러 벌이
되는데, 그걸 없애려고 `code_guide.md`로 합친 것이므로 포인터는 **내용이
완전히 같아야** 한다.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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


def test_the_standard_itself_is_not_a_pointer():
    body = STANDARD.read_text(encoding="utf-8")
    assert len(body.splitlines()) > 200
    assert "## 기능 경계" in body
