"""공개 트렌드의 조기 이동을 운영자에게 전달하고 성공한 전송만 기록한다."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests

from services.telegram_bot.core.clock import ensure_jst, now
from services.telegram_bot.core.config import (
    DATA_DIR,
    TELEGRAM_CHAT_ID,
    WEB_STATUS_BASE_URL,
    WEB_STATUS_TIMEOUT_SECONDS,
)
from services.telegram_bot.core.storage import write_json_atomic

logger = logging.getLogger(__name__)
STATE_FILE = DATA_DIR / "consensus_alert" / "state.json"
# 공개 화면의 고정 주소. 내부 조회는 기존 루프백 WEB_STATUS_BASE_URL을 쓴다.
FORECAST_URL = "https://nunchi.live/forecast"
# 운영자 결정(2026-11-15): 하루 재알림 금지, 다만 이동 폭이 5%p 더 커지면 알림.
RENOTIFY_DELTA = 0.05
# 공개 메시지의 원문·주석에도 출처명과 금지 용어를 노출하지 않는다.
FORBIDDEN_COPY = ("polymarket", "폴리마켓", "베팅", "배팅", "예측시장")


def fetch_trending():
    response = requests.get(f"{WEB_STATUS_BASE_URL}/api/forecast/trending",
                            timeout=WEB_STATUS_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("generation_id"):
        raise ValueError("컨센서스 수집분 없음")
    return payload


def message(rows):
    lines = ["뉴스보다 컨센서스가 먼저 움직인 질문"]
    for row in rows:
        label = row.get("summary") or row["title"]
        probability = f"{row['probability'] * 100:.1f}%"
        if row.get("event_type") != "binary":
            probability = f"{row['leader']} {probability}"
        # 화면은 상세 id URL을 지원하지 않는다. 원문 제목으로 검색한다.
        link = FORECAST_URL + "?" + urlencode({"q": row["title"]})
        lines.append(f"· {label} — 지금 {probability} · 24시간 {row['day_change'] * 100:+.1f}%p"
                     f" · 관련 뉴스 {row['news_count']}건\n  {link}")
    return "\n".join(lines)


async def process(app, payload, *, path=STATE_FILE):
    """전송 실패는 호출자에게 올리고 상태 파일은 그대로 둔다."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    generation = payload["generation_id"]
    if generation == state.get("last_generation"):
        return
    current = now()
    sent = {key: value for key, value in state.get("sent", {}).items()
            if current - ensure_jst(datetime.fromisoformat(value["at"])) <= timedelta(days=7)}
    rows = []
    if payload.get("quiet_moves_state") == "ok":
        for row in payload.get("quiet_moves", []):
            if any(term in str(row.get(field) or "").lower()
                   for term in FORBIDDEN_COPY for field in ("title", "summary", "leader")):
                continue
            if not isinstance(row.get("probability"), (int, float)) or not isinstance(row.get("day_change"), (int, float)):
                continue
            previous = sent.get(str(row["id"]))
            if previous and current - ensure_jst(datetime.fromisoformat(previous["at"])) < timedelta(hours=24):
                # 이진 부동소수점의 0.15 - 0.10도 정확히 5%p 재알림 경계로 본다.
                if abs(row["day_change"]) + 1e-12 < abs(previous["day_change"]) + RENOTIFY_DELTA:
                    continue
            rows.append(row)
    if rows:
        await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message(rows),
                                   disable_web_page_preview=True)
        for row in rows:
            sent[str(row["id"])] = {"at": current.isoformat(), "day_change": row["day_change"]}
    write_json_atomic(path, {"last_generation": generation, "sent": sent})


async def poll(app):
    if not TELEGRAM_CHAT_ID:
        return
    try:
        payload = await asyncio.to_thread(fetch_trending)
        await process(app, payload)
    except Exception:
        logger.exception("컨센서스 조기 이동 알림 실패")


def install_jobs(scheduler, app):
    scheduler.add_job(poll, trigger="cron", minute=25, args=[app], id="consensus_alert",
                      max_instances=1, coalesce=True)
