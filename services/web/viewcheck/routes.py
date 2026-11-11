"""뷰 검증 API(`/api/research/view`) — 계정의 뷰를 컨센서스에 대조하고 포지션 아이디어를 낸다.

**운영자 개인용이다(운영자 지시 2026-11-12). 공개 서비스가 아니다.** `VIEW_CHECK_ACCOUNT_KEYS`에 적힌 계정만 쓰고,
다른 계정에는 화면에도 API에도 드러나지 않는다(404).

개인 리서치와 같은 화면(`/research`)에서 같은 뷰(리서치 주제)로 돈다. 리서치가 "뉴스에서 뷰와 맞는 종목을
찾는 일"이면 이것은 "뷰가 참여자들의 기대와 맞는지 보고, 맞는 만큼만 포지션으로 옮기는 일"이다.

- **동의는 리서치의 것을 쓴다.** 보내는 것이 같다 — 뷰와 관심종목 이름·코드. 자산은 보내지 않는다.
- **요청 밖 스레드에서 돈다.** 세 번의 모델 호출이 1~5분 걸린다(2026-11-12 실측). `POST`가 실행 표시를 남기고
  202로 돌아오며 화면은 `GET`으로 상태를 본다. 스레드는 계정 잠금 없이 돌고, 결과를 쓸 때만 잠금을 잡아 실행
  표시의 id를 다시 확인한다 — 그 사이 탈퇴하면 계정 폴더를 되살리지 않는다(개인 리서치와 같다).
- **실패한 실행도 센다.** 계정 하루 `VIEW_CHECK_MAX_DAILY`회, 서버 하루 `VIEW_CHECK_SERVER_MAX_DAILY`회.
- **검증 결과는 최근 한 건만 둔다.** `GET`은 검증 때 보인 선택지의 지금 확률을 함께 준다(LLM 없음) — 뷰를 받치던
  컨센서스가 그 뒤 움직였는지를 화면이 보인다.

계획서: `services/web/docs/consensus-view-advisor.md`
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.web.accounts import Account, Accounts, account_lock
from services.web.core import config
from services.web.core.clock import now
from services.web.core.storage import FileLockTimeout, file_lock, write_json_atomic
from services.web.llm.viewcheck import ViewCheckError
from services.web.personal_research import load_profile, read
from services.web.polymarket.repository import PolymarketRepository
from services.web.portfolio.store import CANONICAL_CODE, WatchlistStore
from services.web.viewcheck import service

logger = logging.getLogger(__name__)

FORMAT = 1
REPORT_FILE = "research/view.json"
RUN_FILE = "research/view_run.json"
USAGE_FILE = "research/view_usage.json"
NO_CLAIMS = "뷰에서 컨센서스로 확인할 예상을 찾지 못했습니다. 무엇이 어떻게 될지 문장으로 적어 보세요."


class WatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=40)


def enabled(account: Account) -> bool:
    """이 계정이 뷰 검증을 쓰는가. 리서치 화면(`personal_research.py`)이 이 값으로 섹션을 보일지 정한다."""
    return account.key in config.VIEW_CHECK_ACCOUNT_KEYS


def _current(value: Any) -> dict | None:
    return value if isinstance(value, dict) and value.get("format") == FORMAT else None


def _used(root: Path) -> int:
    usage = read(root / USAGE_FILE, {})
    return int(usage.get("count") or 0) if usage.get("day") == now().date().isoformat() else 0


def _run_state(root: Path) -> dict | None:
    run = _current(read(root / RUN_FILE, None))
    if run and run.get("status") == "running":
        started = datetime.fromisoformat(run["started_at"])
        if (now() - started).total_seconds() > config.VIEW_CHECK_RUN_STALE_SECONDS:
            run = {**run, "status": "failed", "error": "검증이 끝나지 않았습니다(서버 재시작 등). 다시 실행하세요."}
    return run


def _reserve_server_quota(users_root: Path) -> bool:
    """서버 전체 하루 상한. 계정 식별값을 담지 않고 탈퇴와 무관하게 남는다(개인 리서치와 같은 꼴)."""
    with file_lock(users_root / ".view-check-usage.lock", stale_seconds=60, timeout=5):
        path = users_root / ".view-check-usage.json"
        quota = read(path, {})
        day = now().date().isoformat()
        used = quota.get("count", 0) if quota.get("day") == day else 0
        if used >= config.VIEW_CHECK_SERVER_MAX_DAILY:
            return False
        write_json_atomic(path, {"day": day, "count": used + 1})
        return True


def build_checker():
    from services.web.llm.factory import build_view_checker
    return build_view_checker()


def check(view: str, watchlist: dict[str, str], repository: PolymarketRepository, checker) -> dict[str, Any]:
    """검증 한 번(블로킹). 전제를 못 찾았거나 판정 호출이 실패하면 `ViewCheckError`.

    포지션 단계만 실패하면 판정까지는 그대로 낸다 — 검증은 포지션 없이도 쓸모가 있다.
    """
    manifest = repository.load()
    claims = checker.claims(view)
    board = service.collect(repository, claims)
    claim_payload, market_payload = service.judge_payload(claims, board)
    judgement = checker.judge(view, claim_payload, market_payload)
    claims_report = service.claims_report(claims, judgement, board)
    blind_spots = service.blind_spots_report(judgement, board)
    positions, status = [], "skipped"
    # 컨센서스로 본 전제가 하나도 없으면 포지션을 내지 않는다. 이 기능이 내는 것은 "검증한 뷰"의 포지션이다.
    if any(claim["verdict"] != "unpriced" for claim in claims_report):
        try:
            picked = checker.positions(
                service.positions_payload(judgement.refined_view or view, claims_report, blind_spots, watchlist),
                service.position_codes(watchlist), [claim["c"] for claim in claims_report])
            positions, status = service.positions_report(picked, claims_report, watchlist), "ok"
        except ViewCheckError as error:
            logger.warning("[VIEW_CHECK] 포지션 단계 실패: %s", error)
            status = "failed"
    return {
        "format": FORMAT, "generated_at": now().isoformat(timespec="seconds"), "view": view,
        "generation_id": manifest.get("generation_id"), "consensus_generated_at": manifest.get("generated_at"),
        "claims": claims_report, "refined_view": judgement.refined_view, "blind_spots": blind_spots,
        "positions": positions, "positions_status": status, "watched": [],
        "disclaimer": service.DISCLAIMER,
    }


def build_view_router(
    accounts: Accounts,
    repository: PolymarketRepository,
    *,
    checker_factory: Callable[[], Any] = build_checker,
    start: Callable[[Callable[[], None]], None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/research/view")
    start = start or (lambda work: threading.Thread(target=work, name="view-check", daemon=True).start())
    slots = threading.BoundedSemaphore(config.VIEW_CHECK_MAX_CONCURRENT)

    def operator(account: Account = Depends(accounts.context)) -> Account:
        # 없는 기능처럼 답한다. 운영자 개인용이라는 사실도 다른 계정에 알릴 것이 아니다.
        if not enabled(account):
            raise HTTPException(404, "Not Found")
        return account

    @router.get("")
    def state(account: Account = Depends(operator)):
        root = account.root
        report = _current(read(root / REPORT_FILE, None))
        return {
            "report": report, "run": _run_state(root),
            "drift": service.drift(repository, report) if report else None,
            "ready": bool(repository.load()),
            "usage": {"used": _used(root), "limit": config.VIEW_CHECK_MAX_DAILY},
        }

    @router.post("", status_code=202)
    def create(account: Account = Depends(operator)):
        root = account.root
        profile = load_profile(root)
        if not profile["consented_at"]:
            raise HTTPException(403, "뷰와 관심종목 이름을 AI 분석에 보내는 데 먼저 동의해야 합니다.")
        view = profile["topic"].strip()
        if not view:
            raise HTTPException(422, "검증할 뷰(관심 주제)를 입력하세요.")
        run = _run_state(root)
        if run and run.get("status") == "running":
            raise HTTPException(409, "뷰 검증이 이미 진행 중입니다. 끝나면 결과가 표시됩니다.")
        if not repository.load():
            raise HTTPException(503, "예측 자료가 아직 없습니다. 잠시 뒤 다시 시도하세요.")
        used = _used(root)
        if used >= config.VIEW_CHECK_MAX_DAILY:
            raise HTTPException(429, f"뷰 검증은 하루 {config.VIEW_CHECK_MAX_DAILY}회까지 실행할 수 있습니다.")
        if not slots.acquire(blocking=False):
            raise HTTPException(409, "다른 검증이 진행 중입니다. 잠시 뒤 다시 시도하세요.")
        try:
            try:
                reserved = _reserve_server_quota(accounts.root)
            except FileLockTimeout as exc:
                raise HTTPException(503, "잠시 뒤 다시 시도하세요.") from exc
            if not reserved:
                raise HTTPException(429, "오늘 서버 전체의 뷰 검증 한도를 모두 썼습니다. 내일 다시 시도하세요.")
            watchlist = WatchlistStore(root / "watchlist.json").get()
            run = {"format": FORMAT, "id": uuid.uuid4().hex, "status": "running",
                   "started_at": now().isoformat(timespec="seconds"), "view": view}
            write_json_atomic(root / USAGE_FILE, {"day": now().date().isoformat(), "count": used + 1})
            write_json_atomic(root / RUN_FILE, run)
        except BaseException:
            slots.release()
            raise
        start(lambda: _work(accounts, account, run, view, watchlist, repository, checker_factory, slots))
        return run

    @router.post("/watch")
    def watch(body: WatchRequest, account: Account = Depends(operator)):
        """최근 검증의 포지션에 있는 상품만 관심종목에 담는다. 모델이 낸 코드는 관심종목 형식 검사도 거친다."""
        report = _current(read(account.root / REPORT_FILE, None))
        match = next((item for item in (report or {}).get("positions", []) if item.get("code") == body.code), None)
        if match is None or not CANONICAL_CODE.fullmatch(body.code):
            raise HTTPException(422, "최근 뷰 검증의 포지션이 아닙니다.")
        store = WatchlistStore(account.root / "watchlist.json")
        items = store.get()
        if len(items) >= config.PORTFOLIO_MAX_WATCHLIST and body.code not in items:
            raise HTTPException(422, f"관심종목은 {config.PORTFOLIO_MAX_WATCHLIST}개까지입니다.")
        items[body.code] = (str(match.get("name") or "").strip() or body.code)[:60]
        store.replace(items)
        report["watched"] = sorted({*report.get("watched", []), body.code})
        write_json_atomic(account.root / REPORT_FILE, report)
        return {"watchlist": items, "watched": report["watched"]}

    return router


def _work(accounts: Accounts, account: Account, run: dict, view: str, watchlist: dict[str, str],
          repository: PolymarketRepository, checker_factory, slots: threading.BoundedSemaphore) -> None:
    """스레드 본문. 검증은 잠금 밖에서, 저장은 계정 잠금 안에서 한다."""
    report, error = None, None
    try:
        report = check(view, watchlist, repository, checker_factory())
    except ViewCheckError as exc:
        logger.warning("[VIEW_CHECK] 검증 실패: %s", exc)
        # 전제 단계가 두 번 다 반려됐으면 모델 장애가 아니라 뷰에서 나눌 예상을 찾지 못한 것이다.
        error = (NO_CLAIMS if exc.stage == "claims" and exc.rejected
                 else "검증 결과를 받지 못했습니다. 다시 실행하세요.")
    except Exception:
        logger.exception("[VIEW_CHECK] 실행 오류")
        error = "뷰 검증을 실행하지 못했습니다. 잠시 뒤 다시 시도하세요."
    finally:
        slots.release()
    try:
        with account_lock(accounts.root, account.key, timeout=60):
            current = _current(read(account.root / RUN_FILE, None))
            if not account.root.exists() or not current or current.get("id") != run["id"]:
                return  # 그 사이 탈퇴했거나 다른 실행이 덮었다. 계정 폴더를 되살리지 않는다.
            if report is not None:
                write_json_atomic(account.root / REPORT_FILE, report)
            write_json_atomic(account.root / RUN_FILE,
                              {**run, "status": "ok" if report is not None else "failed",
                               "finished_at": now().isoformat(timespec="seconds"), "error": error})
    except (FileLockTimeout, OSError, HTTPException):
        logger.exception("[VIEW_CHECK] 결과 저장 실패")
