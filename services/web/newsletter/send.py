"""일일 다이제스트 발송 one-shot — `python -m services.web.newsletter.send`.

systemd timer(`stock-chatbot-newsletter.timer`, 매일 08:30 한국 시간)가 부른다. 07:40 시장
감성 갱신과 08시 시장상황 보고서 판정 뒤라 그날 아침 자료가 들어간다. 웹 서버·봇과 다른
프로세스이고, 공개 산출물과 `users/<계정키>/newsletter.json`만 읽는다.

계정마다 계정 잠금을 잡고 다시 읽은 뒤 보내고 `last_sent_on`을 쓴다. 그래서 같은 날 다시
돌려도(재시도·수동 실행) 이미 받은 계정에는 또 보내지 않고, 탈퇴·해지와 겹쳐도 지운 주소로
나가지 않는다. 실패·잠금 대기가 하나라도 있으면 종료 코드 1로 끝나 systemd에 드러난다.
"""

from __future__ import annotations

import logging
import re
import smtplib
import sys
from datetime import datetime
from pathlib import Path

from services.web.accounts import account_lock
from services.web.core import config
from services.web.core.clock import now
from services.web.core.storage import FileLockTimeout
from services.web.newsletter.digest import build_digest, render
from services.web.newsletter.mailer import Mailer, build_mailer, compose
from services.web.newsletter.subscription import SubscriptionError, SubscriptionStore, unsubscribe_url

logger = logging.getLogger(__name__)
_ACCOUNT_KEY = re.compile(r"[0-9a-f]{64}")


def _subscribers(users_dir: Path) -> list[Path]:
    if not users_dir.is_dir():
        return []
    return sorted(p for p in users_dir.iterdir()
                  if _ACCOUNT_KEY.fullmatch(p.name) and (p / "newsletter.json").is_file())


def run(*, users_dir: Path, public_dir: Path, mailer: Mailer, identity_key: str,
        moment: datetime | None = None, lock_timeout: float = 10.0) -> dict[str, int | str]:
    moment = moment or now()
    day = moment.date().isoformat()
    counts: dict[str, int | str] = {"sent": 0, "skipped": 0, "failed": 0, "busy": 0}

    def due(folder: Path) -> bool:
        record = SubscriptionStore(folder / "newsletter.json").read()
        return bool(record) and record["status"] == "active" and record.get("last_sent_on") != day

    # 잠금 없이 한 번 걸러 SMTP에 붙을 일이 있는지만 본다. 보내기 직전에 잠금 안에서 다시 읽는다.
    pending = []
    for folder in _subscribers(users_dir):
        try:
            if due(folder):
                pending.append(folder)
            else:
                counts["skipped"] += 1
        except SubscriptionError:
            pending.append(folder)  # 잠금 안에서 다시 읽고 실패로 센다
    if not pending:
        return {**counts, "result": "done"}
    # 해지 링크에 서명할 키가 없으면 보내지 않는다. 끌 수 없는 편지를 보내지 않는다.
    if not mailer.configured or len(identity_key) < 32:
        return {**counts, "failed": len(pending), "result": "not_configured"}
    digest = build_digest(public_dir, moment, window_hours=config.NEWSLETTER_WINDOW_HOURS,
                          max_market_age_hours=config.NEWSLETTER_MAX_MARKET_AGE_HOURS,
                          headlines=config.NEWSLETTER_HEADLINES_PER_MARKET)
    if digest is None:
        return {**counts, "result": "no_fresh_data"}

    finished = 0  # 결과(보냄·건너뜀·실패·대기)가 정해진 계정 수
    try:
        with mailer.session() as send:
            for folder in pending:
                try:
                    with account_lock(users_dir, folder.name, timeout=lock_timeout):
                        if not due(folder):
                            counts["skipped"] += 1
                            finished += 1
                            continue
                        subscription = SubscriptionStore(folder / "newsletter.json")
                        record = subscription.read()
                        link = unsubscribe_url(config.AUTH_ORIGIN, identity_key, folder.name, record["email"])
                        text, html = render(digest, site=config.AUTH_ORIGIN, unsubscribe_url=link)
                        send(compose(mailer, record["email"], digest["subject"], text, html, unsubscribe_url=link))
                        subscription.write({**record, "last_sent_on": day})
                        counts["sent"] += 1
                except FileLockTimeout:
                    counts["busy"] += 1
                except (smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError, OSError,
                        SubscriptionError) as error:
                    # 주소는 로그에 남기지 않는다. 계정 키 앞 8자로 어느 계정인지만 짚는다.
                    logger.warning("[NEWSLETTER] 발송 실패 account=%s error=%s",
                                   folder.name[:8], type(error).__name__)
                    counts["failed"] += 1
                finished += 1
    except (smtplib.SMTPException, OSError) as error:
        # 연결·로그인 실패나 도중 끊김. 그 계정과 남은 계정은 못 보냈다 — 다시 돌리면
        # 이미 받은 계정은 `last_sent_on`으로 건너뛴다.
        logger.error("[NEWSLETTER] SMTP 세션 실패: %s", type(error).__name__)
        counts["failed"] += len(pending) - finished
    return {**counts, "result": "done"}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    counts = run(users_dir=config.USERS_DIR, public_dir=config.PUBLIC_DIR, mailer=build_mailer(),
                 identity_key=config.ACCOUNT_IDENTITY_KEY)
    logger.info("[NEWSLETTER] %s", " ".join(f"{k}={v}" for k, v in counts.items()))
    if counts["result"] == "not_configured":
        logger.error("[NEWSLETTER] 구독자가 있는데 SMTP_HOST·NEWSLETTER_FROM 또는 ACCOUNT_IDENTITY_KEY가 비어 있다")
    return 1 if counts["failed"] or counts["busy"] else 0


if __name__ == "__main__":
    sys.exit(main())
