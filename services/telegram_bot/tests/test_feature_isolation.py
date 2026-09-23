"""기능은 서로를 참조하지 않는다.

한 기능을 고쳤을 때 다른 기능이 깨지는 일을 구조적으로 막는다. 실제로 두 번
당했다.

- `news_prefilter`가 `research`의 **비공개** 함수를 직접 불렀다. 리서치의 종목명
  처리를 건드리면 사전선별 매칭이 함께 흔들렸다.
- `system_admin`이 `news_prefilter`의 `report()` dict 모양을 알고 화면을 그렸다.
  관측 항목을 하나 추가하려면 남의 기능 파일을 함께 고쳐야 했다.

기능이 공유해야 하는 것은 **같은 모듈 안의 공용 계층**(`services/telegram_bot/core`,
`services/telegram_bot/llm`, `services/telegram_bot/stocks`,
`services/telegram_bot/state`, `services/telegram_bot/news`, `services/telegram_bot/handlers`)에 두거나,
`FeatureSpec` 선언(`status_reports` 등)을 통해 레지스트리가 중개한다.
"""

import ast
from pathlib import Path

FEATURES_DIR = Path(__file__).resolve().parents[1] / "features"
# 기능이 아니라 기능 프레임워크다. 어디서든 가져다 쓴다.
FRAMEWORK = {"base", "registry"}


def _feature_imports() -> list[tuple[str, str, str, int]]:
    found = []
    for path in sorted(FEATURES_DIR.rglob("*.py")):
        parts = path.relative_to(FEATURES_DIR).parts
        if len(parts) < 2:
            continue  # features/__init__.py 는 카탈로그 조립 지점이다
        owner = parts[0]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            segments = node.module.split(".")
            if segments[:3] != ["services", "telegram_bot", "features"] or len(segments) < 4:
                continue
            other = segments[3]
            if other == owner or other in FRAMEWORK:
                continue
            for alias in node.names:
                found.append((owner, other, alias.name, node.lineno))
    return found


def test_no_feature_imports_another_feature():
    offenders = _feature_imports()

    assert offenders == [], "기능 간 직접 참조: " + ", ".join(
        f"{owner} -> {other}.{name} (line {line})"
        for owner, other, name, line in offenders
    )


def test_system_status_screens_come_from_the_registry_not_hardcoded_names():
    """`/system`의 항목은 각 기능이 선언한다.

    system_admin이 기능 이름을 직접 적으면, 그 기능을 끄거나 이름을 바꿀 때
    남의 파일이 함께 틀어진다.
    """
    from services.telegram_bot.features import ALL_FEATURES, build_feature_registry

    registry = build_feature_registry(feature.key for feature in ALL_FEATURES)
    names = {spec.name for spec in registry.status_reports()}
    assert "prefilter" in names

    source = (FEATURES_DIR / "system_admin" / "handlers.py").read_text(encoding="utf-8")
    assert "prefilter" not in source
    assert "news_prefilter" not in source
