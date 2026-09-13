"""Research analysis result normalization, actions, and Telegram presentation."""

from __future__ import annotations

import html
from typing import Any, Protocol

from telegram_bot.core.config import TELEGRAM_MESSAGE_LIMIT
from telegram_bot.core.telegram_html import truncate_html
_MAX_WATCH_LINES = 10
_MAX_RISK_LINES = 8


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


def _format_action_lines(items: list[dict[str, Any]]) -> str:
    lines = []
    for item in items:
        reason = html.escape(str(item.get("reason") or ""))
        code = html.escape(str(item["code"]))
        name = html.escape(str(item["name"]))
        confidence = float(item.get("confidence") or 0)
        relevance = float(item["relevance"])
        scores = [f"관련도 {relevance:.0%}", f"판단 {confidence:.0%}"]
        suffix = f" - {reason}" if reason else ""
        lines.append(f"- {name} ({code}) [{' · '.join(scores)}]{suffix}")
    return "\n".join(lines) if lines else "- 없음"


def format_result_sections(
    result: dict[str, Any],
    pending: dict[str, list[dict[str, Any]]],
    news_count: int,
    candidate_count: int = 0,
    temporary: bool = False,
) -> list[str]:
    title = "리서치 분석 결과"
    if temporary:
        title += " (임시 리서치)"

    summary = html.escape(result.get("summary") or "요약 없음")
    add_lines = _format_action_lines(pending["add"])
    remove_lines = _format_action_lines(pending["remove"])

    watch_lines = []
    for item in result.get("actions", []):
        if not isinstance(item, dict) or item.get("action") != "watch":
            continue
        code = html.escape(normalize_code(str(item.get("ticker") or "")))
        name = html.escape(str(item.get("name") or code))
        reason = html.escape(str(item.get("reason") or ""))
        relevance = float(item["relevance"])
        line = f"- {name} ({code}) [관련도 {relevance:.0%}]"
        if reason:
            line += f" - {reason}"
        watch_lines.append(line)

    risks = result.get("risks") or []
    risk_lines = "\n".join(f"- {html.escape(str(risk))}" for risk in risks[:_MAX_RISK_LINES]) or "- 없음"

    critique_lines = []
    for item in result.get("view_critique") or []:
        if not isinstance(item, dict):
            continue
        point = html.escape(str(item.get("point") or "").strip())
        if not point:
            continue
        severity = item.get("severity")
        prefix = f"[{float(severity):.0%}] " if severity is not None else ""
        critique_lines.append(f"- {prefix}{point}")
        evidence = item.get("evidence")
        if isinstance(evidence, dict):
            evidence_title = str(evidence.get("title") or "").strip()
            if evidence_title:
                evidence_source = str(evidence.get("source") or "").strip()
                source_part = f" ({html.escape(evidence_source)})" if evidence_source else ""
                critique_lines.append(f"  ↳ {html.escape(evidence_title[:80])}{source_part}")
    critique_text = "\n".join(critique_lines) or "- 상충하는 근거 없음"

    sections = [
        f"<b>{html.escape(title)}</b>\n분석 뉴스: {news_count}건 / 후보 universe: {candidate_count}개",
        f"<b>요약</b>\n{summary}",
        f"<b>🗣 내 뷰 반론</b>\n{critique_text}",
        f"<b>추가 후보</b>\n{add_lines}",
        f"<b>제외 후보</b>\n{remove_lines}",
        f"<b>주목 종목</b>\n{chr(10).join(watch_lines[:_MAX_WATCH_LINES]) or '- 없음'}",
        f"<b>리스크</b>\n{risk_lines}",
    ]
    return [truncate_html(section, TELEGRAM_MESSAGE_LIMIT) for section in sections]
