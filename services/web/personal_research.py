"""계정별 개인 리서치(`/api/research`) — 운영자 봇 리서치와 같은 분석을 계정 주제로 돌린다.

운영자 봇(`telegram_bot/research/job.py`)은 저장된 자연어 주제(관점)와 원문 뉴스·후보 종목·섹터 요약을
분석기에 넣어 시장 흐름 요약, 관심종목 추가·삭제·관찰 제안, 리스크, 주제에 대한 반론을 받는다. 웹 계정도
같은 프롬프트(`prompts/market_research_ko.txt`)·같은 분석기(`llm/market_view.py`)로 돈다(2026-10-05 운영자 지시).
봇과 다른 점은 셋이다.

- **입력 묶음은 봇이 굽는다**(`storage/public/research_inputs.json`, `telegram_bot/research/inputs.py`).
  웹은 봇 코드를 import하지 않고 외부 시세를 부르지 않는다. 묶음은 관심종목 없이 만든 것이라 계정의
  관심종목은 여기서 후보 앞에 붙이고, 그 종목의 현재가·자금흐름은 들어가지 않는다.
- **주제·관심종목 이름이 Cloudflare Workers AI로 간다.** 그래서 계정이 동의(`consented_at`)해야 돌고,
  동의는 언제든 철회한다. 자산·계정 식별값은 보내지 않는다.
- **관심종목은 바꾸지 않고 제안만 한다**(운영자 결정 2026-10-05). 계정이 제안별로 적용 버튼을 누른다.
  봇처럼 추가(add)·삭제(remove)만 적용 대상이고 관찰(watch)은 표시만 한다. 예약 실행도 없다 — 버튼으로만 돈다.

분석은 최대 `RESEARCH_TIMEOUT`초 걸려 요청 안에서 기다리지 않는다. `POST /reports`가 실행 표시(`run.json`)를
남기고 스레드를 띄워 202로 돌아오며, 화면은 `GET`으로 상태를 본다. 스레드는 계정 잠금 없이 분석하고,
결과를 쓸 때만 잠금을 잡아 실행 표시의 id를 다시 확인한다 — 그 사이 탈퇴하면 계정 폴더를 되살리지 않는다.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.web.accounts import Account, Accounts, account_lock
from services.web.core import config
from services.web.core.clock import now
from services.web.core.storage import FileLockTimeout, file_lock, write_json_atomic
from services.web.portfolio.store import WatchlistStore

logger = logging.getLogger(__name__)

# 계정 리서치 파일 형식. 예전 검색형 리서치(형식 없음)는 읽지 않는다 — 현재 형식만 지원한다.
FORMAT = 2
_CANONICAL_CODE = re.compile(
    r"\d{6}|\d{5}|KR:(KOSPI|KOSDAQ):\d{6}|US:(NASDAQ|NYSE):[A-Z][A-Z0-9.-]{0,14}")
_SLOTS = threading.BoundedSemaphore(config.RESEARCH_MAX_CONCURRENT)


class ResearchProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field("", max_length=config.RESEARCH_TOPIC_MAX_CHARS)


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agree: bool


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: str = Field(min_length=1, max_length=40)
    action: Literal["add", "remove"]


def read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (ValueError, OSError) as exc:
        raise HTTPException(500, "개인 리서치 저장 파일을 읽을 수 없습니다.") from exc


def _current(value: Any) -> dict | None:
    return value if isinstance(value, dict) and value.get("format") == FORMAT else None


def _profile(root: Path) -> dict[str, Any]:
    return _current(read(root / "research/profile.json", None)) or {
        "format": FORMAT, "topic": "", "consented_at": None}


def load_inputs(path: Path | None = None) -> dict[str, Any] | None:
    """봇이 구운 입력 묶음. 없거나 형식이 틀리거나 오래됐으면 None(자료 없음)."""
    try:
        data = json.loads((path or config.RESEARCH_INPUTS_FILE).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format") != config.RESEARCH_INPUTS_FORMAT:
            return None
        generated = datetime.fromisoformat(str(data["generated_at"]))
        if not isinstance(data.get("news_items"), list) or not data["news_items"]:
            return None
        if not isinstance(data.get("candidates"), list):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (now() - generated).total_seconds() > config.RESEARCH_INPUTS_MAX_AGE_HOURS * 3600:
        return None
    return data


def candidate_universe(watchlist: dict[str, str], candidates: list[dict]) -> list[dict[str, Any]]:
    """봇과 같은 순서: 관심종목 먼저(묶음에 같은 종목이 있으면 그 근거를 쓴다), 그다음 묶음의 후보."""
    by_code = {str(row.get("code") or ""): row for row in candidates if isinstance(row, dict)}
    watch = [{**by_code.get(code, {"code": code, "name": name, "market": "", "matched_news": []}),
              "code": code, "name": name, "in_watchlist": True}
             for code, name in watchlist.items()]
    others = [{**row, "in_watchlist": False} for code, row in by_code.items()
              if code and code not in watchlist]
    return (watch + others)[: config.RESEARCH_MAX_CANDIDATES]


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """다음 분석의 previous_analyses. 봇(`research/state.py`)과 같은 압축본이다."""
    return {
        "generated_at": result.get("generated_at"),
        "summary": str(result.get("summary") or "")[:300],
        "actions": [{"ticker": str(item.get("ticker") or "").strip(), "action": item.get("action")}
                    for item in result.get("actions", [])
                    if isinstance(item, dict) and item.get("action") in ("add", "remove")
                    and str(item.get("ticker") or "").strip()],
    }


def build_analyzer():
    from services.web.llm.factory import build_research_analyzer
    return build_research_analyzer()


def _reserve_server_quota(users_root: Path) -> bool:
    """서버 전체 하루 상한. 계정 식별값을 담지 않고 탈퇴와 무관하게 남는다."""
    with file_lock(users_root / ".research-usage.lock", stale_seconds=60, timeout=5):
        path = users_root / ".research-usage.json"
        quota = read(path, {})
        day = now().date().isoformat()
        used = quota.get("count", 0) if quota.get("day") == day else 0
        if used >= config.RESEARCH_SERVER_MAX_DAILY:
            return False
        write_json_atomic(path, {"day": day, "count": used + 1})
        return True


def _run_state(root: Path) -> dict | None:
    run = _current(read(root / "research/run.json", None))
    if run and run.get("status") == "running":
        started = datetime.fromisoformat(run["started_at"])
        if (now() - started).total_seconds() > config.RESEARCH_RUN_STALE_SECONDS:
            run = {**run, "status": "failed", "error": "분석이 끝나지 않았습니다(서버 재시작 등). 다시 실행하세요."}
    return run


def build_research_router(
    accounts: Accounts,
    *,
    analyzer_factory: Callable[[], Any] = build_analyzer,
    start: Callable[[Callable[[], None]], None] | None = None,
    inputs_path: Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/research")
    start = start or (lambda work: threading.Thread(target=work, name="personal-research", daemon=True).start())

    @router.get("")
    def latest(account: Account = Depends(accounts.context)):
        root = account.root
        inputs = load_inputs(inputs_path)
        return {
            "profile": _profile(root),
            "report": _current(read(root / "research/latest.json", None)),
            "run": _run_state(root),
            "history": (_current(read(root / "research/history.json", None)) or {}).get("items", []),
            "inputs": {"ready": inputs is not None,
                       "generated_at": inputs.get("generated_at") if inputs else None},
            "limits": {"daily": config.RESEARCH_MAX_DAILY},
        }

    @router.put("/profile")
    def profile(body: ResearchProfile, account: Account = Depends(accounts.context)):
        current = _profile(account.root)
        topic = " ".join(body.topic.split())
        if topic != current["topic"]:
            # 주제가 바뀌면 이전 분석과 비교하지 않는다(봇 `set_sight`와 같다).
            (account.root / "research/history.json").unlink(missing_ok=True)
        value = {**current, "topic": topic}
        write_json_atomic(account.root / "research/profile.json", value)
        return value

    @router.put("/consent")
    def consent(body: ConsentRequest, account: Account = Depends(accounts.context)):
        value = {**_profile(account.root), "consented_at": now().isoformat(timespec="seconds") if body.agree else None}
        write_json_atomic(account.root / "research/profile.json", value)
        return value

    @router.post("/reports", status_code=202)
    def report(account: Account = Depends(accounts.context)):
        root = account.root
        current = _profile(root)
        if not current["consented_at"]:
            raise HTTPException(403, "주제와 관심종목 이름을 AI 분석에 보내는 데 먼저 동의해야 합니다.")
        topic = current["topic"].strip()
        if not topic:
            raise HTTPException(422, "리서치 주제를 입력하세요.")
        run = _run_state(root)
        if run and run.get("status") == "running":
            raise HTTPException(409, "리서치가 이미 진행 중입니다. 끝나면 결과가 표시됩니다.")
        inputs = load_inputs(inputs_path)
        if inputs is None:
            raise HTTPException(503, "분석할 최신 시장 자료가 아직 없습니다. 잠시 뒤 다시 시도하세요.")
        day = now().date().isoformat()
        usage = read(root / "research/usage.json", {})
        used = usage.get("count", 0) if usage.get("day") == day else 0
        if used >= config.RESEARCH_MAX_DAILY:
            raise HTTPException(429, f"개인 리서치는 하루 {config.RESEARCH_MAX_DAILY}회까지 실행할 수 있습니다.")
        if not _SLOTS.acquire(blocking=False):
            raise HTTPException(409, "다른 리서치가 진행 중입니다. 잠시 뒤 다시 시도하세요.")
        try:
            if not _reserve_server_quota(accounts.root):
                raise HTTPException(429, "오늘 서버 전체의 리서치 한도를 모두 썼습니다. 내일 다시 시도하세요.")
            watchlist = WatchlistStore(root / "watchlist.json").get()
            history = (_current(read(root / "research/history.json", None)) or {}).get("items", [])
            run = {"format": FORMAT, "id": uuid.uuid4().hex, "status": "running",
                   "started_at": now().isoformat(timespec="seconds"), "topic": topic}
            write_json_atomic(root / "research/usage.json", {"day": day, "count": used + 1})
            write_json_atomic(root / "research/run.json", run)
        except BaseException:
            _SLOTS.release()
            raise
        start(lambda: _work(accounts, account, run, topic, watchlist, inputs, history, analyzer_factory))
        return run

    @router.post("/actions")
    def apply(body: ApplyRequest, account: Account = Depends(accounts.context)):
        """최근 결과에 있는 추가·삭제 제안만 적용한다. 모델이 지어낸 코드는 관심종목 형식 검사도 거친다."""
        report = _current(read(account.root / "research/latest.json", None))
        actions = (report or {}).get("result", {}).get("actions", [])
        match = next((item for item in actions
                      if item.get("ticker") == body.ticker and item.get("action") == body.action), None)
        if match is None or not _CANONICAL_CODE.fullmatch(body.ticker):
            raise HTTPException(422, "최근 리서치 결과의 제안이 아닙니다.")
        store = WatchlistStore(account.root / "watchlist.json")
        items = store.get()
        if body.action == "add":
            if len(items) >= config.PORTFOLIO_MAX_WATCHLIST and body.ticker not in items:
                raise HTTPException(422, f"관심종목은 {config.PORTFOLIO_MAX_WATCHLIST}개까지입니다.")
            items[body.ticker] = (str(match.get("name") or "").strip() or body.ticker)[:60]
        else:
            items.pop(body.ticker, None)
        store.replace(items)
        report["applied"] = {**report.get("applied", {}), body.ticker: body.action}
        write_json_atomic(account.root / "research/latest.json", report)
        return {"watchlist": items, "applied": report["applied"]}

    return router


def _work(accounts: Accounts, account: Account, run: dict, topic: str, watchlist: dict[str, str],
          inputs: dict, history: list, analyzer_factory) -> None:
    """스레드 본문. 분석은 잠금 밖에서, 저장은 계정 잠금 안에서 한다."""
    from services.web.llm.market_view import MarketViewError

    result, error = None, None
    try:
        news_items = inputs["news_items"]
        candidates = candidate_universe(watchlist, inputs["candidates"])
        result = analyzer_factory().analyze(
            topic, watchlist, news_items, candidates, inputs.get("sector_summary_context"), history)
    except MarketViewError as exc:
        logger.warning("[RESEARCH] 개인 리서치 분석 실패: %s", exc)
        error = "분석 결과를 받지 못했습니다. 다시 실행하세요."
    except Exception:
        logger.exception("[RESEARCH] 개인 리서치 실행 오류")
        error = "리서치를 실행하지 못했습니다. 잠시 뒤 다시 시도하세요."
    finally:
        _SLOTS.release()
    try:
        with account_lock(accounts.root, account.key, timeout=60):
            current = _current(read(account.root / "research/run.json", None))
            if not account.root.exists() or not current or current.get("id") != run["id"]:
                return  # 그 사이 탈퇴했거나 다른 실행이 덮었다. 계정 폴더를 되살리지 않는다.
            finished = now().isoformat(timespec="seconds")
            if result is not None:
                write_json_atomic(account.root / "research/latest.json", {
                    "format": FORMAT, "generated_at": result["generated_at"], "topic": topic,
                    "inputs_generated_at": inputs.get("generated_at"),
                    "news_count": len(inputs["news_items"]), "watchlist_count": len(watchlist),
                    "result": result, "applied": {},
                })
                items = ((_current(read(account.root / "research/history.json", None)) or {}).get("items", [])
                         + [summarize(result)])[-config.RESEARCH_HISTORY_LIMIT:]
                write_json_atomic(account.root / "research/history.json", {"format": FORMAT, "items": items})
            write_json_atomic(account.root / "research/run.json",
                              {**run, "status": "ok" if result is not None else "failed",
                               "finished_at": finished, "error": error})
    except (FileLockTimeout, OSError, HTTPException):
        logger.exception("[RESEARCH] 개인 리서치 결과 저장 실패")
