"""뉴스레터 라우트 둘.

- `/api/account/newsletter` — 구독·확인·해지. 로그인 계정의 잠금 안에서 돈다.
- `/newsletter/unsubscribe` — 편지 안의 원클릭 해지. 로그인 없이 쓰는 공개 쓰기라 무엇을
  지울지는 서명(`unsubscribe_token`)으로만 정하고, 같은 계정 잠금 안에서 지운다.
"""

from __future__ import annotations

import hmac
import logging
import re
import smtplib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from services.web.accounts import Account, Accounts, account_lock
from services.web.core import config
from services.web.core.clock import now
from services.web.core.storage import FileLockTimeout
from services.web.newsletter.mailer import build_mailer, compose
from services.web.newsletter.subscription import (
    ACCOUNT_KEY,
    CodeRejected,
    SubscriptionError,
    SubscriptionStore,
    codes_sent_on,
    confirm_code,
    issue_code,
    normalize_email,
    unsubscribe_token,
)
from services.web.pages.newsletter import (
    UNSUBSCRIBE_CONFIRM_HTML,
    UNSUBSCRIBE_DONE_HTML,
    UNSUBSCRIBE_INVALID_HTML,
)

_TOKEN = re.compile(r"[0-9a-f]{64}")

logger = logging.getLogger(__name__)


class EmailIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=12)


def build_newsletter_router(accounts: Accounts, *, mailer_factory=None) -> APIRouter:
    router = APIRouter(prefix="/api/account/newsletter")

    def store(account: Account = Depends(accounts.context)) -> SubscriptionStore:
        return SubscriptionStore(account.root / "newsletter.json")

    def read(subscription: SubscriptionStore):
        try:
            return subscription.read()
        except SubscriptionError as error:
            raise HTTPException(500, "뉴스레터 구독 파일을 읽을 수 없습니다.") from error

    def view(record, mailer) -> dict[str, Any]:
        return {
            "configured": mailer.configured,
            "status": record["status"] if record else "none",
            "email": record["email"] if record else "",
            "last_sent_on": record.get("last_sent_on") if record else None,
            "codes_today": codes_sent_on(record, now()),
            "codes_per_day": config.NEWSLETTER_CODES_PER_DAY,
        }

    @router.get("")
    def get(subscription: SubscriptionStore = Depends(store)) -> dict[str, Any]:
        return view(read(subscription), (mailer_factory or build_mailer)())

    @router.put("")
    def subscribe(body: EmailIn, subscription: SubscriptionStore = Depends(store)) -> dict[str, Any]:
        mailer = (mailer_factory or build_mailer)()
        if not mailer.configured:
            raise HTTPException(503, "메일 발송을 준비 중입니다.")
        email = normalize_email(body.email)
        if email is None:
            raise HTTPException(422, "이메일 주소 형식이 아닙니다.")
        moment = now()
        try:
            record, code = issue_code(read(subscription), email, moment,
                                      minutes=config.NEWSLETTER_CODE_MINUTES,
                                      per_day=config.NEWSLETTER_CODES_PER_DAY)
        except OverflowError as error:
            raise HTTPException(429, str(error)) from error
        # 보내기 전에 저장한다. 발송이 실패해도 횟수는 센다 — 상한이 발송 성공에만 걸리면
        # 실패를 되풀이하는 요청이 상한 밖에서 SMTP를 두드린다.
        subscription.write(record)
        try:
            mailer.send(compose(
                mailer, email, "[눈치] 뉴스레터 확인 코드",
                f"눈치 뉴스레터 확인 코드: {code}\n\n"
                f"{config.NEWSLETTER_CODE_MINUTES}분 안에 내 자산 화면에 입력하세요.\n"
                "직접 요청하지 않았다면 이 메일을 무시하세요. 확인하지 않은 주소로는 뉴스레터를 보내지 않습니다.",
            ))
        except (smtplib.SMTPException, OSError) as error:
            logger.warning("[NEWSLETTER] 확인 코드 발송 실패: %s", type(error).__name__)
            raise HTTPException(502, "확인 메일을 보내지 못했습니다. 잠시 뒤 다시 시도하세요.") from error
        return view(record, mailer)

    @router.post("/confirm")
    def confirm(body: CodeIn, subscription: SubscriptionStore = Depends(store)) -> dict[str, Any]:
        record = read(subscription)
        if record is None:
            raise HTTPException(404, "구독 신청이 없습니다.")
        try:
            record = confirm_code(record, body.code.strip(), now(), attempts=config.NEWSLETTER_CODE_ATTEMPTS)
        except CodeRejected as error:
            if hasattr(error, "record"):
                subscription.write(error.record)
            raise HTTPException(410 if error.final else 422, str(error)) from error
        subscription.write(record)
        return view(record, (mailer_factory or build_mailer)())

    @router.delete("", status_code=204)
    def unsubscribe(subscription: SubscriptionStore = Depends(store)) -> None:
        # 해지하면 주소도 남기지 않는다. 다시 받으려면 확인부터 새로 한다.
        subscription.delete()

    return router


def build_unsubscribe_router(accounts: Accounts) -> APIRouter:
    router = APIRouter()

    def well_formed(a: str, t: str) -> bool:
        return bool(ACCOUNT_KEY.fullmatch(a) and _TOKEN.fullmatch(t))

    @router.api_route("/newsletter/unsubscribe", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def confirm_page(a: str = "", t: str = "") -> HTMLResponse:
        # 여는 것만으로는 끄지 않는다(메일 보안 검사기의 미리 열기). 버튼이 POST를 보낸다.
        if not well_formed(a, t):
            return HTMLResponse(UNSUBSCRIBE_INVALID_HTML, status_code=400)
        return HTMLResponse(UNSUBSCRIBE_CONFIRM_HTML)

    @router.post("/newsletter/unsubscribe", response_class=HTMLResponse)
    def one_click(a: str = "", t: str = "") -> HTMLResponse:
        # 메일 서비스의 원클릭 해지(RFC 8058)는 다른 출처에서 본문
        # `List-Unsubscribe=One-Click`으로 부른다. 그래서 Origin을 요구하지 않고 본문도 읽지 않는다 —
        # 권한은 서명 하나다.
        if not accounts.configured:
            raise HTTPException(503, "뉴스레터 해지를 준비 중입니다.")
        if not well_formed(a, t):
            return HTMLResponse(UNSUBSCRIBE_INVALID_HTML, status_code=400)
        folder = accounts.root / a
        if not folder.is_dir():
            return HTMLResponse(UNSUBSCRIBE_DONE_HTML)
        try:
            with account_lock(accounts.root, a, timeout=5):
                subscription = SubscriptionStore(folder / "newsletter.json")
                record = subscription.read()
                # 이미 꺼졌으면 같은 결과를 돌려준다. 메일 서비스는 같은 해지를 여러 번 보낸다.
                if record is None:
                    return HTMLResponse(UNSUBSCRIBE_DONE_HTML)
                expected = unsubscribe_token(accounts.identity_key, a, record["email"])
                if not hmac.compare_digest(t, expected):
                    return HTMLResponse(UNSUBSCRIBE_INVALID_HTML, status_code=400)
                subscription.delete()
        except FileLockTimeout as error:
            raise HTTPException(503, "잠시 뒤 다시 시도하세요.") from error
        except SubscriptionError as error:
            raise HTTPException(500, "뉴스레터 구독 파일을 읽을 수 없습니다.") from error
        return HTMLResponse(UNSUBSCRIBE_DONE_HTML)

    return router
