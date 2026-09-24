"""도메인끼리는 코드를 공유하지 않는다 — 데이터만 `storage/`로 공유한다.

어느 한 도메인의 것이 아니라 둘 사이의 경계라 저장소 자체의 검사로 둔다.
예전의 유일한 예외(봇 → 웹 `services/web/export.py` 지연 import)는 봇 쪽
`services/telegram_bot/publish.py`로 옮겨 없앴다. 다시 생기면 한쪽 사정이 다른 쪽
기동을 막는 옛 `shared/` 사고가 형태만 바꿔 돌아온다.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = {
    "services.telegram_bot": ROOT / "services" / "telegram_bot",
    "services.web": ROOT / "services" / "web",
}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_bot_and_web_never_import_each_other():
    crossings = []
    for owner, folder in DOMAINS.items():
        others = [name for name in DOMAINS if name != owner]
        for path in folder.rglob("*.py"):
            for name in _imports(path):
                if any(name == other or name.startswith(other + ".") for other in others):
                    crossings.append(f"{path.relative_to(ROOT)} → {name}")
    assert crossings == []


def test_shorts_never_imports_the_repository():
    crossings = [
        f"{path.relative_to(ROOT)} → {name}"
        for path in (ROOT / "shorts" / "src").rglob("*.py")
        for name in _imports(path)
        if name == "services" or name.startswith("services.")
    ]
    assert crossings == []
