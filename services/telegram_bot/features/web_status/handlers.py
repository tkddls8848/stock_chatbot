"""텔레그램 관리 패널의 웹 상태 명령(`/web`).

봇은 웹 코드를 import하지 않는다. shorts처럼 **공개 웹의 GET API를 HTTP로** 읽는다 —
웹 프로세스가 죽었으면 그 사실 자체가 가장 먼저 보여야 할 상태이기도 하다.
개인 화면(`/portfolio`)은 잠겨 있으므로 HTTP로 읽지 않고, 공유 저장소의
`storage/portfolio/advice/latest.json`을 파일로 읽는다(마지막 조언 시각·외부 자료 상태만).
"""

import asyncio
import html
import json
import logging
from pathlib import Path
from typing import Any, Callable

import requests
from telegram import Update
from telegram.ext import ContextTypes

from services.telegram_bot.core.config import PORTFOLIO_DIR, WEB_STATUS_BASE_URL, WEB_STATUS_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

_FRESHNESS = {
    "normal": "제때 갱신",
    "warming_up": "주기 파악 중",
    "delayed": "지연",
    "stale": "오래됨",
    "missing": "자료 없음",
}


def _stamp(value: Any) -> str:
    text = str(value or "").replace("T", " ")
    return text[:16] if text else "없음"


def _get(path: str, fetch: Callable[..., Any]) -> dict[str, Any] | None:
    try:
        response = fetch(f"{WEB_STATUS_BASE_URL}{path}", timeout=WEB_STATUS_TIMEOUT_SECONDS)
    except requests.RequestException:
        return None
    if getattr(response, "status_code", 500) != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


_SOURCE_LABELS = {"deposit_rates": "금감원", "market_rates": "한국은행", "real_estate": "국토부"}
_SOURCE_STATES = {"ok": "정상", "missing_key": "키 없음", "error": "실패"}


def portfolio_status_line(folder: Path = PORTFOLIO_DIR) -> str:
    """마지막 자산 조언의 시각과 외부 자료 상태. 조언 본문·자산은 텔레그램에 옮기지 않는다."""
    try:
        latest = json.loads((folder / "advice" / "latest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "자산 조언: 아직 없음"
    except (OSError, ValueError):
        return "자산 조언: 기록 파일을 읽지 못함"
    sources = " · ".join(
        f"{_SOURCE_LABELS.get(k, k)} {_SOURCE_STATES.get(v, v)}" for k, v in (latest.get("sources") or {}).items()
    )
    body = "본문 있음" if latest.get("llm_status") == "ok" else "진단만"
    return html.escape(f"자산 조언: {_stamp(latest.get('created_at'))} · {body} · {sources}")


def build_web_status(fetch: Callable[..., Any] = requests.get) -> str:
    """관리 패널에 보일 웹 상태 몇 줄. 블로킹이므로 스레드에서 부른다."""
    meta = _get("/api/meta", fetch)
    if meta is None:
        return "<b>🌐 웹 상태</b>\n웹이 응답하지 않습니다(8788). stock-chatbot-web 서비스를 확인하세요."
    lines = [
        "<b>🌐 웹 상태</b> (한국 시간)",
        f"시장 감성: {_stamp(meta.get('market_generated_at'))}",
        f"리서치: {_stamp(meta.get('research_generated_at'))}",
        f"뉴스 검색(/search) 자료: {_stamp(meta.get('news_generated_at'))}",
    ]
    health = _get("/api/forecast/health", fetch)
    if health is None:
        lines.append("예측 컨센서스: 자료 없음")
    else:
        freshness = health.get("freshness") or {}
        state = _FRESHNESS.get(str(freshness.get("state")), str(freshness.get("state") or "미상"))
        lines.append(
            f"예측 컨센서스 수집: {_stamp(freshness.get('last_success_at'))} · {state}"
            # 정상 결과는 적지 않는다. 예산 초과·실패 같은 예외만 보인다.
            + (f" · 마지막 시도 {html.escape(str(health['last_result']))}"
               if health.get("last_result") not in (None, "ok", "success") else "")
        )
    brief = _get("/api/forecast/sector-brief", fetch)
    lines.append(f"예측 컨센서스 줄글: {_stamp((brief or {}).get('written_at'))}")
    trending = _get("/api/forecast/trending", fetch)
    lines.append(f"예측 컨센서스 트렌드: {_stamp((trending or {}).get('written_at'))}")
    events = _get("/api/forecast/events?page_size=1", fetch)
    index = (events or {}).get("search_index") or {}
    if index.get("total"):
        # 예측 질문 검색용 주석 진행률이다. 뉴스 검색(/search)과는 별개다.
        lines.append(f"예측 질문 한국어 검색 준비: {int(index.get('annotated') or 0):,}/{int(index['total']):,}건")
    lines.append(portfolio_status_line())
    return "\n".join(lines)


async def cmd_web(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = await asyncio.to_thread(build_web_status)
    await message.reply_text(text, parse_mode="HTML")
