"""계정별 뉴스레터 구독 파일과 확인 코드.

`users/<계정키>/newsletter.json` 한 파일이다. 계정 잠금 안에서만 읽고 쓴다 — 웹 요청은
`Accounts.context`가, 발송 one-shot은 `account_lock`이 같은 잠금을 잡는다. 탈퇴하면 계정
폴더와 함께 지워지고 `/api/account/export`에 그대로 담긴다.

형식(현재 형식만 읽는다)::

    {"email": str, "status": "pending" | "active",
     "code": {"salt", "hash", "expires_at", "attempts"} | null,
     "codes_sent": [ISO 시각, ...], "confirmed_at": ISO | null,
     "last_sent_on": "YYYY-MM-DD" | null, "updated_at": ISO}

확인 코드는 남의 주소를 등록하지 못하게 하는 장치다. 코드 원문은 저장하지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from services.web.core.clock import ensure_jst
from services.web.core.storage import write_json_atomic

# RFC 5322 전부가 아니라 실제로 메일을 받는 주소의 흔한 모양이다. 줄바꿈이 끼면
# 헤더 주입이 되므로 fullmatch로만 받는다.
_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_CODE = re.compile(r"\d{6}")


class SubscriptionError(RuntimeError):
    """구독 파일이 깨졌다. 없는 구독으로 삼키지 않는다."""


class CodeRejected(ValueError):
    """코드가 틀렸거나 만료됐다. `final`이면 새 코드를 받아야 한다."""

    def __init__(self, message: str, *, final: bool):
        super().__init__(message)
        self.final = final


def normalize_email(raw: str) -> str | None:
    value = raw.strip()
    if len(value) > 254 or not _EMAIL.fullmatch(value):
        return None
    local, domain = value.rsplit("@", 1)
    return f"{local}@{domain.lower()}"


def _digest(salt: str, code: str) -> str:
    return hashlib.sha256((salt + code).encode()).hexdigest()


class SubscriptionStore:
    def __init__(self, path: Path):
        self._path = path

    def read(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise SubscriptionError(f"{self._path.name}: {type(error).__name__}") from error
        if not isinstance(payload, dict) or payload.get("status") not in {"pending", "active"} \
                or not isinstance(payload.get("email"), str):
            raise SubscriptionError(f"{self._path.name}: 형식이 다르다")
        return payload

    def write(self, record: dict[str, Any]) -> None:
        write_json_atomic(self._path, record, indent=2)

    def delete(self) -> None:
        self._path.unlink(missing_ok=True)


def codes_sent_on(record: dict[str, Any] | None, moment: datetime) -> int:
    """한국 시간 달력 하루에 보낸 확인 코드 수."""
    day = moment.date()
    return sum(1 for stamp in (record or {}).get("codes_sent", [])
               if ensure_jst(datetime.fromisoformat(stamp)).date() == day)


def issue_code(record: dict[str, Any] | None, email: str, moment: datetime, *,
               minutes: int, per_day: int) -> tuple[dict[str, Any], str]:
    """새 주소로 대기 상태를 만들고 코드 원문을 돌려준다. 하루 상한을 넘으면 `OverflowError`.

    이미 받고 있던 구독도 주소를 다시 넣으면 확인할 때까지 멈춘다 — 확인하지 않은 주소로
    편지가 나가면 확인 절차가 의미 없다.
    """
    if codes_sent_on(record, moment) >= per_day:
        raise OverflowError(f"확인 코드는 하루 {per_day}번까지 보낼 수 있습니다.")
    code = f"{secrets.randbelow(10**6):06d}"
    salt = secrets.token_hex(8)
    stamp = moment.isoformat(timespec="seconds")
    recent = [s for s in (record or {}).get("codes_sent", [])
              if ensure_jst(datetime.fromisoformat(s)).date() == moment.date()]
    return {
        "email": email,
        "status": "pending",
        "code": {"salt": salt, "hash": _digest(salt, code),
                 "expires_at": (moment + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
                 "attempts": 0},
        "codes_sent": [*recent, stamp],
        "confirmed_at": None,
        "last_sent_on": (record or {}).get("last_sent_on"),
        "updated_at": stamp,
    }, code


def confirm_code(record: dict[str, Any], code: str, moment: datetime, *, attempts: int) -> dict[str, Any]:
    """맞으면 구독을 켠 새 기록을, 틀리면 시도 수를 올린 기록과 함께 `CodeRejected`를 낸다.

    틀린 시도도 저장해야 하므로 예외에 갱신된 기록을 실어 보낸다(`error.record`).
    """
    pending = record.get("code")
    if record.get("status") != "pending" or not isinstance(pending, dict):
        raise CodeRejected("확인할 코드가 없습니다. 코드를 다시 받으세요.", final=True)
    if moment >= ensure_jst(datetime.fromisoformat(pending["expires_at"])) or pending["attempts"] >= attempts:
        raise CodeRejected("코드가 만료되었습니다. 코드를 다시 받으세요.", final=True)
    if not _CODE.fullmatch(code) or not hmac.compare_digest(_digest(pending["salt"], code), pending["hash"]):
        used = pending["attempts"] + 1
        error = CodeRejected(
            "코드가 맞지 않습니다." + (f" {attempts - used}번 더 입력할 수 있습니다." if used < attempts
                                     else " 코드를 다시 받으세요."),
            final=used >= attempts)
        error.record = {**record, "code": {**pending, "attempts": used}}
        raise error
    stamp = moment.isoformat(timespec="seconds")
    return {**record, "status": "active", "code": None, "confirmed_at": stamp, "updated_at": stamp}


# ── 편지 안의 원클릭 해지 ───────────────────────────────────────────────────
# 로그인 없이 쓰는 공개 쓰기라, 무엇을 지울지는 서명으로만 정한다. 서명은 계정 키와
# 주소를 묶는다 — 주소를 바꾸면 옛 편지의 링크로 새 구독을 끌 수 없다.
ACCOUNT_KEY = re.compile(r"[0-9a-f]{64}")


def unsubscribe_token(identity_key: str, account_key: str, email: str) -> str:
    # 계정 경로를 정하는 키를 그대로 쓰지 않고 용도별로 파생한다.
    purpose = hmac.new(identity_key.encode(), b"newsletter-unsubscribe", hashlib.sha256).digest()
    return hmac.new(purpose, f"{account_key}\n{email}".encode(), hashlib.sha256).hexdigest()


def unsubscribe_url(origin: str, identity_key: str, account_key: str, email: str) -> str:
    token = unsubscribe_token(identity_key, account_key, email)
    return f"{origin.rstrip('/')}/newsletter/unsubscribe?a={account_key}&t={token}"
