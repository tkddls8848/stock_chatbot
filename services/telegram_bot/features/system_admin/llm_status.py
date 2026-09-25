"""LLM 회로 관측과 운영 채팅의 하루 한 번 소진 알림."""

import json
from datetime import datetime, timezone

from services.telegram_bot.core.clock import JST
from services.telegram_bot.core.config import DATA_DIR, TELEGRAM_CHAT_ID
from services.telegram_bot.core.storage import write_json_atomic
from services.telegram_bot.llm.backends import last_quota_exhaustion, open_circuits

NOTICE_FILE = DATA_DIR / "system_admin" / "llm_quota_notice.json"


def _korean_time(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=JST).strftime("%Y-%m-%d %H:%M")


async def render_llm_status(_bot_data) -> str:
    circuits = open_circuits()
    if not circuits:
        return "LLM 회로: 정상"
    # 무료 한도와 영구 장애를 짧은 일시 오류보다 먼저 보여 준다.
    state = max(circuits, key=lambda item: (
        item.reason == "quota_exhausted", item.open_until is None, item.open_until or 0,
    ))
    reason = {
        "quota_exhausted": "할당량 소진",
        "auth_error": "인증 실패",
        "bad_request": "요청 설정 오류",
        "rate_limited": "요청 속도 제한",
    }.get(state.reason, "공급자 오류")
    release = (
        f"{_korean_time(state.open_until)} 한국 시간"
        if state.open_until is not None else "설정 확인 후 재시작 필요"
    )
    return f"LLM 회로: 열림 · {reason} · 해제: {release}"


async def notify_quota_exhaustion(app) -> None:
    state = last_quota_exhaustion()
    if state is None:
        return
    day = datetime.fromtimestamp(state.opened_at, tz=timezone.utc).date().isoformat()
    try:
        saved = json.loads(NOTICE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        # 깨진 기록은 "보낸 적 없음"이다. 30초마다 같은 예외로 죽는 것보다 한 번 더 보내는 편이 낫다.
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    if saved.get("utc_date", "") >= day:
        return
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=(
            "⚠️ LLM 할당량 소진\n"
            f"소진: {_korean_time(state.opened_at)} 한국 시간\n"
            f"해제: {_korean_time(state.open_until)} 한국 시간\n"
            "중단 작업: 보고서·브리핑·리서치·시장 감성\n"
            "조치: Cloudflare 유료 전환 또는 같은 계정의 다른 사용처 확인"
        ),
    )
    # 보낸 뒤에 기록한다. 발송이 실패하면 다음 주기(30초)가 다시 보낸다. 발송 직후
    # 죽으면 재기동이 한 번 더 보낼 수 있지만, 운영 알림은 중복보다 누락이 비싸다.
    write_json_atomic(NOTICE_FILE, {"utc_date": day})
