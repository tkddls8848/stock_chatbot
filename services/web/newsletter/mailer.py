"""SMTP 발송. 확인 코드(웹 요청)와 일일 다이제스트(one-shot)가 같이 쓴다.

평문 경로는 두지 않는다 — 465는 처음부터 TLS, 그 밖의 포트는 STARTTLS를 요구하고
서버가 지원하지 않으면 실패한다. 비밀번호와 수신 주소는 로그·예외 메시지에 남기지 않는다.
"""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from services.web.core import config


@dataclass(frozen=True)
class Mailer:
    host: str
    port: int
    username: str
    password: str
    sender: str
    timeout: float

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)

    @contextmanager
    def session(self) -> Iterator[Callable[[EmailMessage], None]]:
        """연결 하나로 여러 통을 보낸다. 구독자마다 다시 로그인하지 않는다."""
        context = ssl.create_default_context()
        if self.port == 465:
            client = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
        else:
            client = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        with client:
            if self.port != 465:
                client.starttls(context=context)
            if self.username:
                client.login(self.username, self.password)
            yield client.send_message

    def send(self, message: EmailMessage) -> None:
        with self.session() as send:
            send(message)


def build_mailer() -> Mailer:
    return Mailer(config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USERNAME, config.SMTP_PASSWORD,
                  config.NEWSLETTER_FROM, config.SMTP_TIMEOUT)


def compose(mailer: Mailer, to: str, subject: str, text: str, html: str | None = None, *,
            unsubscribe_url: str | None = None) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr(("눈치", mailer.sender))
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate()
    message["Message-ID"] = make_msgid(domain=mailer.sender.rsplit("@", 1)[-1])
    if unsubscribe_url:
        # 메일 서비스의 "구독 취소" 버튼이 이 주소로 POST한다(RFC 8058 원클릭 해지).
        # Gmail·Yahoo는 대량 발송자에게 이 두 헤더를 요구한다.
        message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    return message
