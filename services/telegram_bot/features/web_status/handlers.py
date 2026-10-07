"""텔레그램 관리 패널의 웹 상태 명령(`/web`).

봇은 웹 코드를 import하지 않는다. shorts처럼 **공개 웹의 GET API를 HTTP로** 읽는다 —
웹 프로세스가 죽었으면 그 사실 자체가 가장 먼저 보여야 할 상태이기도 하다.
자산 진단의 외부 자료(금감원·한국은행·국토부)는 앱 기록이 아니라 **지금 그 키로 실제 응답이 오는지** 직접
묻는다(`source_status_rows`). 상태 패널은 서버 위에서 지금 도는 시스템을 검수하는 자리다 — 예전에는 마지막 자산
조언 기록을 읽어 9월 24일의 "한국은행 키 없음"이 키를 넣은 뒤에도 계속 보였다(운영자 지적 2026-10-08).
"""

import asyncio
import html
import logging
from datetime import timedelta
from typing import Any, Callable
from xml.etree import ElementTree

import requests
from telegram import Update
from telegram.ext import ContextTypes

from services.telegram_bot.core.clock import now, today
from services.telegram_bot.core.config import (
    SOURCE_PROBE_KEYS,
    SOURCE_PROBE_TIMEOUT_SECONDS,
    WEB_STATUS_BASE_URL,
    WEB_STATUS_TIMEOUT_SECONDS,
)
from services.telegram_bot.core.telegram_html import MARK_BUSY, MARK_FAIL, MARK_NONE, MARK_OK, MARK_WARN, status_table

logger = logging.getLogger(__name__)

# 수집 신선도 → (화면 문구, 표 기호).
_FRESHNESS = {
    "normal": ("제때 갱신", MARK_OK),
    "warming_up": ("주기 파악 중", MARK_BUSY),
    "delayed": ("지연", MARK_WARN),
    "stale": ("오래됨", MARK_FAIL),
    "missing": ("자료 없음", MARK_FAIL),
}


def _stamp(value: Any) -> str:
    """표의 시각 칸("10/08 07:40", 한국 시간). 연도는 뺀다 — 칸이 좁을수록 휴대폰에서 줄이 안 접힌다."""
    text = str(value or "").replace("T", " ")
    return f"{text[5:7]}/{text[8:10]} {text[11:16]}" if len(text) >= 16 else ""


def _dated(stamp: str, label: str) -> tuple[str, str, str]:
    return (MARK_OK, stamp, label) if stamp else (MARK_NONE, "-", label + " · 자료 없음")


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


# 탐침은 (값, 사유)를 돌려준다. 값은 표의 맞춤 칸이라 ASCII만 쓴다("OK"가 정상).
def _probe_fss(key: str, fetch) -> tuple[str, str]:
    response = fetch("https://finlife.fss.or.kr/finlifeapi/depositProductsSearch.json",
                     params={"auth": key, "topFinGrpNo": "020000", "pageNo": 1}, timeout=SOURCE_PROBE_TIMEOUT_SECONDS)
    if response.status_code != 200:
        return f"HTTP {response.status_code}", "응답 실패"
    code = str((response.json().get("result") or {}).get("err_cd", ""))
    return ("OK", "") if code == "000" else (code or "-", "응답 실패")


def _probe_ecos(key: str, fetch) -> tuple[str, str]:
    response = fetch(f"https://ecos.bok.or.kr/api/KeyStatisticList/{key}/json/kr/1/1",
                     timeout=SOURCE_PROBE_TIMEOUT_SECONDS)
    if response.status_code != 200:
        return f"HTTP {response.status_code}", "응답 실패"
    body = response.json()
    if "KeyStatisticList" in body:
        return "OK", ""
    return str((body.get("RESULT") or {}).get("CODE") or "-"), "응답 실패"


def _probe_molit(key: str, fetch) -> tuple[str, str]:
    month = (today().replace(day=1) - timedelta(days=1)).strftime("%Y%m")
    response = fetch("https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
                     params={"serviceKey": key, "LAWD_CD": "11110", "DEAL_YMD": month, "numOfRows": 1, "pageNo": 1},
                     timeout=SOURCE_PROBE_TIMEOUT_SECONDS)
    if response.status_code != 200:
        # 공공데이터포털은 키 문제를 XML 본문의 사유로 알린다("등록되지 않은 서비스키").
        try:
            reason = (ElementTree.fromstring(response.text).findtext(".//returnAuthMsg") or "").strip()
        except ElementTree.ParseError:
            reason = ""
        return f"HTTP {response.status_code}", reason or "응답 실패"
    code = (ElementTree.fromstring(response.text).findtext(".//resultCode") or "").strip()
    return ("OK", "") if code in ("", "00", "000") else (code, "응답 실패")


_SOURCE_PROBES = (("금감원", "FSS_API_KEY", _probe_fss), ("한국은행", "ECOS_API_KEY", _probe_ecos),
                  ("국토부", "MOLIT_API_KEY", _probe_molit))


def source_status_rows(fetch: Callable[..., Any] = requests.get,
                       keys: dict[str, str] = SOURCE_PROBE_KEYS) -> list[tuple[str, str, str]]:
    """자산 진단 외부 자료를 지금 직접 묻는다. 키가 없으면 묻지 않는다. 오류 문구에 키가 든 주소를 싣지 않는다."""
    rows = []
    for label, name, probe in _SOURCE_PROBES:
        key = keys.get(name, "")
        if not key:
            rows.append((MARK_NONE, "-", f"{label} · 키 없음"))
            continue
        try:
            value, reason = probe(key, fetch)
        except requests.RequestException:
            value, reason = "-", "연결 실패"
        except (ValueError, ElementTree.ParseError):
            value, reason = "-", "응답 형식 오류"
        rows.append((MARK_OK, value, label) if value == "OK" else (MARK_FAIL, value, f"{label} · {reason}"))
    return rows


def _notes(lines: list[str]) -> str:
    return "".join(f"\n· {html.escape(line)}" for line in lines)


def build_web_status(fetch: Callable[..., Any] = requests.get) -> str:
    """관리 패널에 보일 웹 상태 표. 블로킹이므로 스레드에서 부른다."""
    title = f"<b>🌐 웹 상태</b> · {now():%m/%d %H:%M} 확인(한국 시간)"
    meta = _get("/api/meta", fetch)
    if meta is None:
        return f"{title}\n{MARK_FAIL} 웹이 응답하지 않습니다(8788). stock-chatbot-web 서비스를 확인하세요."
    web = status_table([
        _dated(_stamp(meta.get("market_generated_at")), "시장 감성"),
        _dated(_stamp(meta.get("research_generated_at")), "리서치"),
        _dated(_stamp(meta.get("news_generated_at")), "뉴스 검색(/search) 자료"),
    ])

    rows, notes = [], []
    health = _get("/api/forecast/health", fetch)
    if health is None:
        rows.append((MARK_NONE, "-", "수집 · 자료 없음"))
    else:
        freshness = health.get("freshness") or {}
        state, mark = _FRESHNESS.get(str(freshness.get("state")), (str(freshness.get("state") or "미상"), MARK_NONE))
        # 정상 결과는 적지 않는다. 예산 초과·실패 같은 예외만 보인다.
        if health.get("last_result") not in (None, "ok", "success"):
            notes.append(f"수집 마지막 시도: {health['last_result']}")
            mark = MARK_WARN if mark == MARK_OK else mark
        stamp = _stamp(freshness.get("last_success_at"))
        rows.append((mark if stamp else MARK_NONE, stamp or "-", f"수집 · {state}"))
    brief = _get("/api/forecast/sector-brief", fetch) or {}
    counts = brief.get("group_counts") or {}
    # 줄글이 써진 시각만으로는 해설이 하나도 안 나온 실행을 구분하지 못한다. 예외만 붙인다.
    exceptions = [f"{label} {counts[key]}" for key, label in
                  (("stale", "직전 단락"), ("facts_only", "확률만"), ("empty", "정리 못한 분야")) if counts.get(key)]
    stamp = _stamp(brief.get("written_at"))
    if stamp and exceptions:
        rows.append((MARK_WARN, stamp, f"줄글 · 해설 {counts.get('ok', 0)}개"))
        notes.append("줄글 예외: " + " · ".join(exceptions))
    else:
        rows.append(_dated(stamp, "줄글"))
    rows.append(_dated(_stamp((_get("/api/forecast/trending", fetch) or {}).get("written_at")), "트렌드"))
    index = ((_get("/api/forecast/events?page_size=1", fetch) or {}).get("search_index") or {})
    if index.get("total"):
        # 예측 질문 검색용 주석 진행률이다. 뉴스 검색(/search)과는 별개다.
        done, total = int(index.get("annotated") or 0), int(index["total"])
        rows.append((MARK_OK if done >= total else MARK_BUSY, f"{done:,}/{total:,}", "질문 한국어 검색 준비"))

    return "\n\n".join([
        f"{title}\n{web}",
        f"<b>예측 컨센서스</b>\n{status_table(rows)}{_notes(notes)}",
        f"<b>자산 진단 외부 자료</b> · 지금 확인\n{status_table(source_status_rows(fetch))}",
    ])


async def cmd_web(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = await asyncio.to_thread(build_web_status)
    await message.reply_text(text, parse_mode="HTML")
