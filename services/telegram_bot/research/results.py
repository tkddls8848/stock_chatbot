"""리서치 결과의 종목 코드 정규화와 관심종목 추가·삭제 후보 추리기."""

from __future__ import annotations

from typing import Any, Protocol


class StockCodeResolver(Protocol):
    def resolve_code(self, code: str) -> str | None: ...

    def get_display_name(self, code: str) -> str | None: ...


def normalize_code(code: str) -> str:
    raw = str(code).strip()
    if ":" in raw or any(char.isalpha() for char in raw):
        return raw.upper()
    value = "".join(char for char in raw if char.isdigit())
    if not value:
        return raw
    if len(value) <= 5:
        return value.zfill(5)
    return value.zfill(6)


def collect_actions(
    result: dict[str, Any],
    watchlist: dict[str, str],
    stock_db: StockCodeResolver,
) -> dict[str, list[dict[str, Any]]]:
    add_items: list[dict[str, Any]] = []
    remove_items: list[dict[str, Any]] = []
    seen_add: set[str] = set()
    seen_remove: set[str] = set()

    for item in result.get("actions", []):
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        if action not in {"add", "remove", "watch"}:
            continue
        confidence = float(item.get("confidence") or 0)
        relevance = float(item["relevance"])

        code = normalize_code(str(item.get("ticker") or ""))
        if not code:
            continue
        code = stock_db.resolve_code(code) or code

        if action == "add":
            if code in watchlist or code in seen_add:
                continue
            name = str(item.get("name") or "").strip() or stock_db.get_display_name(code) or code
            add_items.append(
                {
                    "code": code,
                    "name": name,
                    "reason": str(item.get("reason") or "").strip(),
                    "confidence": confidence,
                    "relevance": relevance,
                }
            )
            seen_add.add(code)
        elif action == "remove":
            if code in watchlist and code not in seen_remove:
                remove_items.append(
                    {
                        "code": code,
                        "name": watchlist[code],
                        "reason": str(item.get("reason") or "").strip(),
                        "confidence": confidence,
                        "relevance": relevance,
                    }
                )
                seen_remove.add(code)

    return {"add": add_items, "remove": remove_items}

