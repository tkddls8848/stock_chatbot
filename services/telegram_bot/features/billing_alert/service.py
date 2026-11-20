"""서비스별 조회 실패도 알리고, 성공적으로 전송한 알림만 기록한다."""

import asyncio
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import requests

from services.telegram_bot.core.clock import JST, now
from services.telegram_bot.core.config import (
    BILLING_CLOUDFLARE_ALERT_USD,
    BILLING_CLOUDFLARE_FREE_NEURONS_PER_DAY,
    BILLING_CLOUDFLARE_USD_PER_1K_NEURONS,
    BILLING_ELEVENLABS_MIN_CREDITS,
    BILLING_MODEL_DEPRECATION_NOTICE_DAYS,
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_AI_BASE_URL,
    CLOUDFLARE_WORKER_AI_API_TOKEN,
    DATA_DIR,
    ELEVENLABS_API_KEY,
    TELEGRAM_CHAT_ID,
)
from services.telegram_bot.core.storage import write_json_atomic

logger = logging.getLogger(__name__)
STATE_FILE = DATA_DIR / "billing_alert" / "state.json"
HTTP_TIMEOUT_SECONDS = 20
# 쇼츠 max_script_chars와 같은 계약 값. 다른 모듈의 코드는 import하지 않는다.
SCRIPT_CHARS = 1_100
CLOUDFLARE_QUERY = """
query($accountTag: string!, $start: Date!, $end: Date!) {
  viewer { accounts(filter: {accountTag: $accountTag}) {
    aiInferenceAdaptiveGroups(limit: 10000, filter: {date_geq: $start, date_leq: $end}) {
      sum { totalNeurons } dimensions { date modelId }
    }
  } }
}
"""


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("잘못된 사용량")
    return value


def check_elevenlabs(current):
    response = requests.get(
        "https://api.elevenlabs.io/v1/user/subscription",
        headers={"xi-api-key": ELEVENLABS_API_KEY}, timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    remaining = _number(payload["character_limit"]) - _number(payload["character_count"])
    reset = payload.get("next_character_count_reset_unix")
    reset_text = ""
    if reset is not None:
        _number(reset)
        reset_text = f" 리셋 {datetime.fromtimestamp(reset, JST):%m-%d}"
    # 리셋 값이 없는 응답도 경고하되, 알려지지 않은 주기는 한 번만 알린다.
    cycle = str(reset) if reset is not None else "unknown"
    if remaining >= BILLING_ELEVENLABS_MIN_CREDITS:
        return None
    return cycle, (
        f"ElevenLabs 남은 크레딧 {remaining:,.0f}자 — 쇼츠 약 {max(0, int(remaining // SCRIPT_CHARS))}편 분량. "
        f"다 쓰면 쇼츠 음성이 기본 음성으로 바뀐다.{reset_text}"
    )


def _cloudflare_usage(start, end):
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_WORKER_AI_API_TOKEN:
        raise ValueError("Cloudflare 자격증명 없음")
    response = requests.post(
        f"{CLOUDFLARE_AI_BASE_URL}/graphql",
        headers={"Authorization": f"Bearer {CLOUDFLARE_WORKER_AI_API_TOKEN}"},
        json={"query": CLOUDFLARE_QUERY, "variables": {
            "accountTag": CLOUDFLARE_ACCOUNT_ID, "start": start.isoformat(), "end": end.isoformat(),
        }}, timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        # 권한 부족도 HTTP 200에 errors로 온다. 권한 안내를 붙이되 응답 본문은 남기지 않는다.
        raise PermissionError("GraphQL 오류")
    accounts = payload["data"]["viewer"]["accounts"]
    if not isinstance(accounts, list) or len(accounts) != 1:
        raise ValueError("계정 응답 이상")
    rows = accounts[0]["aiInferenceAdaptiveGroups"]
    if not isinstance(rows, list) or len(rows) >= 10_000:
        raise ValueError("사용량 응답 이상 또는 잘림")
    for row in rows:
        day = date.fromisoformat(row["dimensions"]["date"])
        if not start <= day <= end:
            raise ValueError("조회 기간 밖의 사용량")
    return rows


def check_cloudflare(current):
    end = current.astimezone(timezone.utc).date()
    start = end.replace(day=1)
    daily = {}
    for row in _cloudflare_usage(start, end):
        day = date.fromisoformat(row["dimensions"]["date"])
        neurons = Decimal(str(_number(row["sum"]["totalNeurons"])))
        daily[day] = daily.get(day, Decimal(0)) + neurons
    excess = sum((max(Decimal(0), total - BILLING_CLOUDFLARE_FREE_NEURONS_PER_DAY)
                  for total in daily.values()), Decimal(0))
    cost = excess * Decimal(str(BILLING_CLOUDFLARE_USD_PER_1K_NEURONS)) / 1000
    if cost < Decimal(str(BILLING_CLOUDFLARE_ALERT_USD)):
        return None
    return start.strftime("%Y-%m"), (
        f"Cloudflare Workers AI 이번 달 초과 요금 추정 ${cost:.2f} "
        f"(무료분 초과 {excess:,.0f} Neurons). 청구 주기는 달력 월과 다를 수 있다."
    )


def check_models(current):
    end = current.astimezone(timezone.utc).date()
    used = {row["dimensions"]["modelId"]
            for row in _cloudflare_usage(end - timedelta(days=6), end)}
    response = requests.get(
        f"{CLOUDFLARE_AI_BASE_URL}/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/models/search",
        headers={"Authorization": f"Bearer {CLOUDFLARE_WORKER_AI_API_TOKEN}"},
        params={"per_page": 1000}, timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("success") is False or payload.get("errors"):
        raise ValueError("모델 목록 조회 실패")
    models = payload["result"]
    if not isinstance(models, list) or len(models) >= 1000:
        raise ValueError("모델 목록 응답 이상 또는 잘림")
    today = current.astimezone(JST).date()
    notices = {}
    for model in models:
        name = model["name"]
        if name not in used:
            continue
        for prop in model["properties"]:
            if prop["property_id"] != "planned_deprecation_date" or not prop["value"]:
                continue
            planned = date.fromisoformat(prop["value"])
            days = (planned - today).days
            if days > BILLING_MODEL_DEPRECATION_NOTICE_DAYS:
                continue
            remaining = f"{days}일 남음" if days >= 0 else f"{-days}일 지남"
            notices[name] = (planned.isoformat(), (
                f"Cloudflare 모델 폐기 예정 — {name} {planned}({remaining}). "
                "그날부터 그 모델 호출이 410으로 실패한다. "
                "봇·웹은 .env CLOUDFLARE_MODEL, 쇼츠는 shorts config.py를 바꿔야 한다."
            ))
    return notices


async def poll(app, *, path=None):
    if not TELEGRAM_CHAT_ID:
        return
    path = STATE_FILE if path is None else path
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("상태 형식 이상")
    except FileNotFoundError:
        state = {}
    except (ValueError, OSError):
        logger.warning("billing_alert 상태를 읽지 못했다")
        state = {}
    current = now()
    day = current.astimezone(JST).date().isoformat()
    lines = []
    updates = {}
    for key, label, check, permission in (
        ("elevenlabs", "ElevenLabs", check_elevenlabs, "API 키에 user_read 권한 필요"),
        ("cloudflare", "Cloudflare Workers AI", check_cloudflare, "토큰에 Account Analytics 읽기 권한 필요"),
        ("models", "Cloudflare 모델 폐기 일정", check_models,
         "토큰에 Account Analytics 읽기·Workers AI 읽기 권한 필요"),
    ):
        if key == "elevenlabs" and not ELEVENLABS_API_KEY:
            continue
        try:
            result = await asyncio.to_thread(check, current)
        except Exception as exc:
            # 예외 문자열·traceback에는 응답 본문이나 인증 정보가 들어갈 수 있다.
            response = getattr(exc, "response", None)
            status = response.status_code if response is not None else None
            logger.warning("%s 확인 실패 (HTTP %s)", label, status)
            state_key = f"{key}_failure_day"
            if state.get(state_key) != day:
                detail = f" (HTTP {status})" if status is not None else ""
                if status in (401, 403) or isinstance(exc, PermissionError):
                    detail += f" — {permission}"
                subject = label if key == "models" else f"{label} 잔량"
                lines.append(f"{subject}을 확인하지 못했다{detail}.")
                updates[state_key] = day
            continue
        if key == "models":
            notified = dict(state.get("models_notified", {}))
            for model, (planned, message) in result.items():
                if notified.get(model) != planned:
                    lines.append(message)
                    notified[model] = planned
                    updates["models_notified"] = notified
        elif result is not None:
            cycle, message = result
            state_key = f"{key}_cycle"
            if state.get(state_key) != cycle:
                lines.append(message)
                updates[state_key] = cycle
    if not lines:
        return
    try:
        await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="\n\n".join(lines),
                                   disable_web_page_preview=True)
    except Exception:
        logger.warning("billing_alert 전송 실패")
        return
    write_json_atomic(path, {**state, **updates})


def install_jobs(scheduler, app):
    if not TELEGRAM_CHAT_ID:
        return
    # 등록 뒤 Telegram 초기화가 1초를 넘겨도 기동 확인을 놓치지 않는다.
    scheduler.add_job(poll, trigger="cron", hour=18, minute=30, args=[app], id="billing_alert",
                      next_run_time=now(), misfire_grace_time=None, max_instances=1, coalesce=True)
