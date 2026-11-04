"""예측 컨센서스 질의응답(`/api/forecast/ask`) — 자연어 질문에 지금 열린 예측 질문으로 답한다.

흐름은 넷이다.

1. 1차 검색(`repository.candidates`, LLM 없음)이 후보를 고른다. 모델은 후보 밖의 질문을 고를 수 없으므로
   찾기가 답의 상한이다. 후보가 하나도 없으면 모델을 부르지 않고 "없음"으로 답한다.
2. 답변 호출 한 번(`llm/polymarket_answer.py`)이 관련 질문·물은 선택지·숫자 없는 해설을 낸다. 모델은
   확률 대신 등급만 본다.
3. 서버가 질문마다 한국어 요약·원문 제목·선택지 확률·하루 참여 규모를 detail에서 붙인다. **숫자는 서버가
   쓴다.** 선택지는 이지선다면 많은 답 하나, 다지선다면 위 셋, 여러 질문 묶음이면 높은 칸·50%에 가까운 칸·
   낮은 칸이고 물은 선택지는 늘 보인다(쇼츠 표시 규칙과 같은 생각이지만 쇼츠 코드는 import하지 않는다).
4. 정규화한 질문이 같고 수집분·색인이 같으면 캐시로 돌려준다. 캐시는 Neurons도 하루 상한도 쓰지 않는다.

**로그인하고 동의한 계정만 묻는다.** 질문 원문이 Cloudflare Workers AI로 간다 — 개인 리서치와 같은
기준이다. 계정 하루 `POLYMARKET_ASK_MAX_DAILY`회·서버 하루 `POLYMARKET_ASK_SERVER_MAX_DAILY`회·동시
`POLYMARKET_ASK_MAX_CONCURRENT`개이고, 실패한 호출도 센다(리서치와 같다). 답은 요청 안에서 만든다(2026-11-04 실측
1.3~2.2초) — 그동안 계정 잠금을 잡고 있어 한 계정의 질문은 하나씩 돈다.
질의 로그는 계정 식별값 없이 질문문·고른 질문·비용만 날짜별 파일로 `POLYMARKET_ASK_LOG_DAYS`일 둔다.
계획서: `docs/polymarket-consensus-qa.md`
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
import json
import logging
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.web.accounts import Account, Accounts
from services.web.core import config
from services.web.core.clock import now
from services.web.core.storage import FileLockTimeout, file_lock, write_json_atomic
from services.web.llm.backends import TokenUsage
from services.web.llm.polymarket_answer import Answer, Pick, PolymarketAnswerError
from services.web.llm.polymarket_brief import FORBIDDEN_COPY, outlook
from services.web.polymarket.annotate import NEURONS_PER_INPUT_TOKEN, NEURONS_PER_OUTPUT_TOKEN
from services.web.polymarket.dashboard.storage import read_detail
from services.web.polymarket import relevance
from services.web.polymarket.repository import PolymarketRepository

logger = logging.getLogger(__name__)

FORMAT = 1
STATE_FILE = "forecast/ask.json"
_TYPE = {"binary": "이지선다", "exclusive_multi": "다지선다(하나만 정답)",
         "independent_multi": "여러 질문 묶음", "unknown_multi": "유형 미상"}
# 여러 질문 묶음(사다리)에서 "갈리는 칸"으로 보는 범위. 밖의 칸은 거의 정해진 칸이다.
_BAND = (0.03, 0.97)
# 다지선다의 셋째 줄은 이 확률 이상일 때만 세운다. 숫자가 아무것도 말하지 않는 줄로 자리를 채우지 않는다.
_THIRD_MIN = 0.01
_LABEL_MAX_CHARS = 80
# 질문에 적은 기준 숫자("1500원")를 선택지 이름("↑1,600")에서 찾는다. 세 자리 이상만 — "12월"·"3번"은 기준이
# 아니다. "2026년"처럼 해를 말하는 숫자는 뺀다. 같은 숫자가 없으면 이 비율 안에서 가장 가까운 칸이다.
_ASKED_NUMBER = re.compile(r"\d[\d,]{2,}(?:\.\d+)?(?!\s*년)")
_LABEL_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NEAREST_RATIO = 0.25
# 물은 선택지는 질문당 이만큼까지다. 모델이 이보다 많이 찍으면 짚은 것이 아니라 "전부"라서 버린다
# (실측 2026-11-04: 휴전 날짜 사다리 11칸, 금값 7칸을 다 찍었다).
_FOCUS_MAX = 2


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=config.POLYMARKET_ASK_QUESTION_MAX_CHARS)


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agree: bool


@dataclass
class Row:
    """후보 하나: 모델에 보이는 꼴과, 답을 조립할 때 쓰는 원자료."""

    event: dict[str, Any]
    summary: str | None
    markets: list[dict[str, Any]]
    options: dict[int, str]  # 모델의 선택지 번호 m → market id(이지선다는 "yes"·"no")
    model: dict[str, Any]


def _probability(market: dict[str, Any]) -> float:
    return float(market["yes_probability"])


def _no(market: dict[str, Any]) -> float:
    value = market.get("no_probability")
    return float(value) if isinstance(value, (int, float)) else 1.0 - _probability(market)


def _label(market: dict[str, Any]) -> str:
    label = " ".join(str(market.get("outcome_label") or market.get("question") or "").split())
    return label[:_LABEL_MAX_CHARS]


def priced_markets(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """확률을 쓸 수 있는 선택지. 열리지 않은 칸(`active: False`)은 뺀다 — "Company H"·"Other" 같은 자리채움 칸이
    기본값 50%로 들어 있어, 두면 다지선다 위 셋·사다리의 50% 칸을 차지한다(2026-11-04 실측).
    """
    return [
        market for market in detail.get("markets") or []
        if isinstance(market, dict) and not market.get("closed") and market.get("active") is not False
        and market.get("price_valid")
        and isinstance(market.get("yes_probability"), (int, float))
    ]


def _binary(event: dict[str, Any], markets: list[dict[str, Any]]) -> bool:
    return event.get("event_type") == "binary" and len(markets) == 1


def _participation(volume: Any) -> str:
    value = float(volume or 0.0)
    return "큼" if value >= 100_000 else "보통" if value >= 5_000 else "작음"


def build_row(number: int, event: dict[str, Any], summary: str | None, markets: list[dict[str, Any]]) -> Row:
    """모델 입력 한 칸. 확률 숫자는 넣지 않고 등급(`outlook`)만 넣는다."""
    if _binary(event, markets):
        market = markets[0]
        shown = [{"m": 1, "label": "예", "outlook": outlook(_probability(market))},
                 {"m": 2, "label": "아니오", "outlook": outlook(_no(market))}]
        options = {1: "yes", 2: "no"}
    else:
        ranked = sorted(markets, key=_probability, reverse=True)[: config.POLYMARKET_ASK_OPTIONS_PER_EVENT]
        shown = [{"m": m, "label": _label(market), "outlook": outlook(_probability(market))}
                 for m, market in enumerate(ranked, start=1)]
        options = {m: str(market.get("id")) for m, market in enumerate(ranked, start=1)}
    model = {"n": number, "title": str(event.get("title") or ""), "summary": summary,
             "type": _TYPE.get(str(event.get("event_type")), "유형 미상"),
             "ends": str(event.get("end_date") or "")[:10],
             "participation": _participation(event.get("volume24hr")), "options": shown}
    if not summary:
        del model["summary"]
    return Row(event, summary, markets, options, model)


def shown_options(event: dict[str, Any], markets: list[dict[str, Any]], focus: set[str]) -> list[dict[str, Any]]:
    """화면에 세울 선택지와 확률. 물은 선택지(`focus`)는 규칙 밖이어도 보인다."""
    if _binary(event, markets):
        sides = {"yes": _probability(markets[0]), "no": _no(markets[0])}
        major = "yes" if sides["yes"] >= sides["no"] else "no"
        keys = [major] + [side for side in ("yes", "no") if side != major and side in focus]
        return [{"label": "예" if side == "yes" else "아니오", "probability": round(sides[side], 4),
                 "focus": side in focus} for side in keys]
    ranked = sorted(markets, key=_probability, reverse=True)
    if event.get("event_type") == "independent_multi":
        band = [market for market in ranked if _BAND[0] <= _probability(market) <= _BAND[1]]
        if len(band) >= 3:
            middle = min(band[1:-1], key=lambda market: abs(_probability(market) - 0.5))
            chosen = [band[0], middle, band[-1]]
        else:
            chosen = sorted(ranked, key=lambda market: abs(_probability(market) - 0.5))[:3]
    else:
        chosen = ranked[:2] + [market for market in ranked[2:3] if _probability(market) >= _THIRD_MIN]
    picked = {str(market.get("id")) for market in chosen}
    chosen += [market for market in ranked if str(market.get("id")) in focus and str(market.get("id")) not in picked]
    chosen.sort(key=_probability, reverse=True)
    return [{"label": _label(market), "probability": round(_probability(market), 4),
             "focus": str(market.get("id")) in focus} for market in chosen]


def _numbers(text: str, pattern: re.Pattern[str]) -> list[float]:
    return [float(value.replace(",", "")) for value in pattern.findall(text)]


def asked_markets(question: str, terms: list[relevance.Term], markets: list[dict[str, Any]]) -> set[str]:
    """질문이 이름을 댄 선택지(서버가 정한다). 질문 낱말(용어집 표기 포함)이 이름에 든 칸 — "인하"는 decrease,
    "엔비디아"는 NVIDIA — 과, 질문의 기준 숫자와 같거나 가장 가까운 칸. 확률이 높은 순으로 `_FOCUS_MAX`개까지.
    """
    named = [market for market in markets if relevance.mentions(_label(market), terms)]
    for asked in _numbers(question, _ASKED_NUMBER):
        scored = [(min(abs(value - asked) for value in values), market) for market in markets
                  if (values := _numbers(_label(market), _LABEL_NUMBER))]
        if scored:
            gap, nearest = min(scored, key=lambda pair: pair[0])
            if gap <= asked * _NEAREST_RATIO:
                named += [market for distance, market in scored if distance == gap]
    named = sorted({str(market.get("id")): market for market in named}.values(), key=_probability, reverse=True)
    return {str(market.get("id")) for market in named[:_FOCUS_MAX]}


def _item(row: Row, pick: Pick, question: str, terms: list[relevance.Term]) -> dict[str, Any] | None:
    # 물은 선택지는 서버가 먼저 정하고, 서버가 못 찾았을 때만 모델이 짚은 칸(두 칸 이하)을 쓴다.
    focus: set[str] = set()
    if not _binary(row.event, row.markets):
        focus = asked_markets(question, terms, row.markets)
    if not focus and len(pick.focus) <= _FOCUS_MAX:
        focus = {row.options[m] for m in pick.focus if m in row.options}
    event = row.event
    item = {
        "id": str(event.get("id")),
        "title": str(event.get("title") or ""),
        "summary": row.summary,
        "category_label": event.get("category_label"),
        "event_type": event.get("event_type"),
        "end_date": event.get("end_date"),
        "volume24hr": event.get("volume24hr"),
        "direct": pick.direct,
        "options": shown_options(event, row.markets, focus),
    }
    # 원문 제목·선택지 이름도 공개 문구다. 출처 서비스 이름 같은 금지어가 들면 그 질문은 내지 않는다.
    text = " ".join([item["title"], row.summary or "", *(option["label"] for option in item["options"])])
    return None if FORBIDDEN_COPY.search(text) else item


def assemble(question: str, manifest: dict[str, Any], rows: list[Row], answer: Answer) -> dict[str, Any]:
    terms = relevance.compile_tokens(relevance.expand(relevance.query_tokens(question)))
    items = [item for pick in answer.picks if (item := _item(rows[pick.n - 1], pick, question, terms)) is not None]
    answerable = answer.answerable and any(item["direct"] for item in items)
    # 직접 답이라던 질문이 전부 빠졌으면 그 질문을 두고 쓴 해설도 내지 않는다.
    commentary = answer.commentary if answerable == answer.answerable else None
    return {"question": question, "answerable": answerable, "commentary": commentary, "items": items,
            "generation_id": manifest.get("generation_id"), "generated_at": manifest.get("generated_at")}


def candidate_rows(repository: PolymarketRepository, question: str) -> list[Row]:
    """1차 검색 후보를 모델 입력 꼴로. detail을 못 읽거나 확률이 없는 후보는 뺀다."""
    rows: list[Row] = []
    for event, summary in repository.candidates(question, limit=config.POLYMARKET_ASK_CANDIDATES):
        try:
            markets = priced_markets(read_detail(repository.root, event))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if markets:
            rows.append(build_row(len(rows) + 1, event, summary, markets))
    return rows


def _neurons(usage: TokenUsage | None) -> float | None:
    if usage is None:
        return None
    if usage.neurons is not None:
        return float(usage.neurons)
    if usage.input_tokens is not None and usage.output_tokens is not None:
        return usage.input_tokens * NEURONS_PER_INPUT_TOKEN + usage.output_tokens * NEURONS_PER_OUTPUT_TOKEN
    return None


def normalize_question(question: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", question).split())


class _Cache:
    """같은 질문 + 같은 수집분의 답. 프로세스 메모리에만 둔다(재시작하면 비운다)."""

    def __init__(self, size: int):
        self._size = size
        self._items: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple) -> dict[str, Any] | None:
        with self._lock:
            if key not in self._items:
                return None
            self._items.move_to_end(key)
            return self._items[key]

    def put(self, key: tuple, value: dict[str, Any]) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (ValueError, OSError) as exc:
        raise HTTPException(500, "질문 기록 파일을 읽을 수 없습니다.") from exc


def _state(root: Path) -> dict[str, Any]:
    value = _read(root / STATE_FILE, None)
    if isinstance(value, dict) and value.get("format") == FORMAT:
        return value
    return {"format": FORMAT, "consented_at": None, "day": None, "count": 0}


def _used(state: dict[str, Any]) -> int:
    return int(state.get("count") or 0) if state.get("day") == now().date().isoformat() else 0


def _public(state: dict[str, Any]) -> dict[str, Any]:
    return {"consented_at": state.get("consented_at"), "used": _used(state),
            "limit": config.POLYMARKET_ASK_MAX_DAILY}


def _reserve_server_quota(users_root: Path) -> bool:
    """서버 전체 하루 상한. 계정 식별값을 담지 않고 탈퇴와 무관하게 남는다(개인 리서치와 같은 꼴)."""
    with file_lock(users_root / ".forecast-ask-usage.lock", stale_seconds=60, timeout=5):
        path = users_root / ".forecast-ask-usage.json"
        quota = _read(path, {})
        day = now().date().isoformat()
        used = quota.get("count", 0) if quota.get("day") == day else 0
        if used >= config.POLYMARKET_ASK_SERVER_MAX_DAILY:
            return False
        write_json_atomic(path, {"day": day, "count": used + 1})
        return True


def log_question(users_root: Path, row: dict[str, Any]) -> None:
    """질의 로그 한 줄(계정 식별값 없음). 날짜별 파일이고, 그날 첫 줄을 쓸 때 보존 기간이 지난 파일을 지운다."""
    folder = users_root / ".forecast-ask-log"
    day = now().date()
    path = folder / f"{day.isoformat()}.jsonl"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            cutoff = (day - timedelta(days=config.POLYMARKET_ASK_LOG_DAYS)).isoformat()
            for old in folder.glob("*.jsonl"):
                if old.stem < cutoff:
                    old.unlink(missing_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": now().isoformat(timespec="seconds"), **row}, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("[FORECAST_ASK] 질의 로그를 남기지 못했습니다.")


def build_answerer():
    from services.web.llm.factory import build_polymarket_answerer
    return build_polymarket_answerer()


def build_ask_router(
    accounts: Accounts,
    repository: PolymarketRepository,
    *,
    answerer_factory: Callable[[], Any] = build_answerer,
) -> APIRouter:
    router = APIRouter(prefix="/api/forecast/ask")
    cache = _Cache(config.POLYMARKET_ASK_CACHE_SIZE)
    slots = threading.BoundedSemaphore(config.POLYMARKET_ASK_MAX_CONCURRENT)

    @router.get("")
    def state(account: Account = Depends(accounts.context)):
        return _public(_state(account.root))

    @router.put("/consent")
    def consent(body: ConsentRequest, account: Account = Depends(accounts.context)):
        value = {**_state(account.root),
                 "consented_at": now().isoformat(timespec="seconds") if body.agree else None}
        write_json_atomic(account.root / STATE_FILE, value)
        return _public(value)

    @router.post("")
    def ask(body: AskRequest, account: Account = Depends(accounts.context)):
        current = _state(account.root)
        if not current.get("consented_at"):
            raise HTTPException(403, "질문을 AI 처리에 보내는 데 먼저 동의해야 합니다.")
        question = normalize_question(body.question)
        if len(question) < 2:
            raise HTTPException(422, "질문을 입력하세요.")
        manifest = repository.load()
        if not manifest:
            raise HTTPException(503, "예측 자료가 아직 없습니다. 잠시 뒤 다시 시도하세요.")
        used = _used(current)
        usage = {"used": used, "limit": config.POLYMARKET_ASK_MAX_DAILY}
        key = (question.casefold(), manifest.get("generation_id"), repository.index_version())
        if (hit := cache.get(key)) is not None:
            log_question(accounts.root, {"question": question, "cached": True, "answerable": hit["answerable"],
                                         "events": [item["id"] for item in hit["items"]]})
            return {**hit, "cached": True, "usage": usage}
        rows = candidate_rows(repository, question)
        if not rows:
            payload = assemble(question, manifest, rows, Answer(False, [], None))
            cache.put(key, payload)
            log_question(accounts.root, {"question": question, "cached": False, "answerable": False,
                                         "events": [], "candidates": 0})
            return {**payload, "cached": False, "usage": usage}
        if used >= config.POLYMARKET_ASK_MAX_DAILY:
            raise HTTPException(429, f"질문은 하루 {config.POLYMARKET_ASK_MAX_DAILY}회까지 할 수 있습니다.")
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "질문이 몰려 있습니다. 잠시 뒤 다시 시도하세요.")
        started = time.monotonic()
        try:
            try:
                reserved = _reserve_server_quota(accounts.root)
            except FileLockTimeout as exc:
                raise HTTPException(503, "잠시 뒤 다시 시도하세요.") from exc
            if not reserved:
                raise HTTPException(429, "오늘 서버 전체의 질문 한도를 모두 썼습니다. 내일 다시 시도하세요.")
            write_json_atomic(account.root / STATE_FILE,
                              {**current, "day": now().date().isoformat(), "count": used + 1})
            usage = {**usage, "used": used + 1}
            try:
                answer = answerer_factory().answer(question, [row.model for row in rows])
            except config.ConfigurationError as exc:
                logger.error("[FORECAST_ASK] LLM 설정 없음: %s", exc)
                raise HTTPException(503, "지금은 질문에 답할 수 없습니다.") from exc
            except PolymarketAnswerError as exc:
                logger.warning("[FORECAST_ASK] 답변 실패: %s", exc)
                log_question(accounts.root, {"question": question, "cached": False, "error": "answer_failed",
                                             "candidates": len(rows),
                                             "ms": round((time.monotonic() - started) * 1000)})
                raise HTTPException(502, "답을 만들지 못했습니다. 잠시 뒤 다시 시도하세요.") from exc
        finally:
            slots.release()
        payload = assemble(question, manifest, rows, answer)
        cache.put(key, payload)
        spent = [value for value in map(_neurons, answer.usages) if value is not None]
        log_question(accounts.root, {
            "question": question, "cached": False, "answerable": payload["answerable"],
            "events": [item["id"] for item in payload["items"]], "candidates": len(rows),
            "calls": len(answer.usages), "neurons": round(sum(spent), 2) if spent else None,
            "ms": round((time.monotonic() - started) * 1000),
        })
        return {**payload, "cached": False, "usage": usage}

    return router
