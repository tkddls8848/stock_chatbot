"""뷰 검증의 서버 쪽: 전제마다 컨센서스 질문을 찾고, 숫자를 붙이고, 포지션을 조립한다.

모델(`llm/viewcheck.py`)이 하는 일은 셋뿐이다 — 뷰를 전제로 나누고, 후보 번호로 판정하고, 표의 상품
코드로 포지션을 고른다. 그 사이를 여기서 잇는다.

- **찾기**: 전제의 검색어를 질의응답과 같은 1차 검색(`repository.candidates`, LLM 없음)에 넣는다. 뷰 문장을
  통째로 넣으면 한 건도 안 잡힌다(낱말 절반이 맞아야 후보다, 2026-11-11 실측 0건). 그래서 전제 단계가
  짧은 검색어를 낸다. 참여가 얇거나 곧 마감하는 질문은 판정 근거에서 뺀다.
- **숫자**: 확률·상품 이름·주의 문구는 서버가 쓴다. 모델 입력에는 등급(`outlook`)만 간다.
- **규칙**: 근거 전제가 전부 컨센서스와 어긋난 확대·축소 아이디어는 "지켜보기"로 내린다. 프롬프트가 같은
  말을 하지만 모델이 어겨도 화면에는 나가지 않게 서버가 한 번 더 본다.

계획서: `services/web/docs/consensus-view-advisor.md`
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import zip_longest
from typing import Any

from services.web.core import config
from services.web.core.clock import now
from services.web.llm.polymarket_brief import FORBIDDEN_COPY, outlook
from services.web.llm.viewcheck import Claim, Judgement, Position
from services.web.polymarket.ask import Row, build_row, option_probabilities, priced_markets, shown_options
from services.web.polymarket.dashboard.models import ends_before
from services.web.polymarket.dashboard.storage import read_detail
from services.web.polymarket.dashboard.taxonomy import assign_brief_group
from services.web.polymarket.repository import PolymarketRepository
from services.web.viewcheck.instruments import BY_CODE, THEME_OF, describe, for_model

VERDICT_LABEL = {"supported": "컨센서스가 받침", "contradicted": "컨센서스와 어긋남", "mixed": "엇갈림",
                 "unpriced": "확인할 질문 없음"}
STANCE_LABEL = {"long": "비중 확대 검토", "reduce": "비중 축소 검토", "hedge": "헤지 검토", "wait": "지켜보기"}
# 뷰와 무관한 신호를 한 분야가 다 차지하지 않게 한다(연준 회의 질문만 여덟 개가 되지 않게).
_MARKET_PER_GROUP = 3
CAUTION_GEARED = "레버리지·역방향 상품은 하루 수익률을 따릅니다. 오래 들고 가면 기초자산의 움직임과 어긋날 수 있습니다."
CAUTION_ETN = "ETN은 만기가 있고 발행 증권사의 신용 위험을 집니다."
CAUTION_DOWNGRADED = "근거로 든 전제가 전부 컨센서스와 어긋나 '지켜보기'로 내렸습니다."
CAUTION_UNSUPPORTED = "컨센서스가 받쳐 준 전제 없이 뷰에만 기댄 아이디어입니다."
DISCLAIMER = ("참고 정보이며 투자 권유나 투자 자문이 아닙니다. 컨센서스 확률은 참여자들의 기대일 뿐 결과를 "
              "보장하지 않고, AI 판정은 틀릴 수 있습니다. 결정과 책임은 본인에게 있습니다.")


@dataclass
class Board:
    """한 번의 검증에 쓰는 후보판. 번호(`n`)는 전제 후보와 시장 신호를 통틀어 하나씩이다."""

    by_claim: list[list[Row]]
    market: list[Row]
    rows: dict[int, Row]


def _volume(event: dict[str, Any]) -> float:
    value = event.get("volume24hr")
    return float(value) if isinstance(value, (int, float)) else 0.0


def meaningful(event: dict[str, Any], horizon: datetime) -> bool:
    """판정 근거로 삼을 만큼 참여가 있고, 결과 확정을 향해 수렴하는 중이 아닌 질문."""
    liquidity = event.get("liquidity")
    enough = (_volume(event) >= config.VIEW_CHECK_MIN_VOLUME
              or (isinstance(liquidity, (int, float)) and liquidity >= config.VIEW_CHECK_MIN_LIQUIDITY))
    return (enough and not ends_before(event, horizon)
            and event.get("category") not in config.POLYMARKET_TRENDING_EXCLUDED_CATEGORIES)


def _row(repository: PolymarketRepository, number: int, event: dict[str, Any], summary: str | None) -> Row | None:
    try:
        markets = priced_markets(read_detail(repository.root, event))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not markets:
        return None
    row = build_row(number, event, summary, markets)
    # 원문 제목·요약·선택지 이름도 화면에 나간다. 출처 이름 같은 금지어가 든 질문은 후보에서 뺀다.
    text = " ".join([str(event.get("title") or ""), summary or "", *(option["label"] for option in row.model["options"])])
    return None if FORBIDDEN_COPY.search(text) else row


def collect(repository: PolymarketRepository, claims: list[Claim]) -> Board:
    """전제별 후보와, 뷰와 무관하게 지금 참여가 몰린 경제·지정학 질문."""
    manifest = repository.load()
    horizon = now() + timedelta(hours=config.POLYMARKET_MIN_HOURS_TO_END)
    board = Board([], [], {})
    seen: set[str] = set()

    def add(event: dict[str, Any], summary: str | None) -> Row | None:
        row = _row(repository, len(board.rows) + 1, event, summary)
        if row is not None:
            seen.add(str(event.get("id")))
            board.rows[len(board.rows) + 1] = row
        return row

    for claim in claims:
        found = [[pair for pair in repository.candidates(query, limit=config.VIEW_CHECK_CANDIDATES_PER_QUERY)
                  if meaningful(pair[0], horizon)] for query in claim.queries]
        kept: list[Row] = []
        # 검색어를 번갈아 가며 채운다 — 첫 검색어의 결과만으로 전제의 자리를 다 쓰지 않는다.
        for pair in (pair for group in zip_longest(*found) for pair in group if pair is not None):
            if len(kept) >= config.VIEW_CHECK_CANDIDATES_PER_CLAIM:
                break
            if str(pair[0].get("id")) not in seen and (row := add(*pair)) is not None:
                kept.append(row)
        board.by_claim.append(kept)

    annotations = repository.load_index()
    per_group: dict[str, int] = {}
    pool = sorted((event for event in manifest.get("events", [])
                   if event.get("price_status") == "ok" and meaningful(event, horizon)), key=_volume, reverse=True)
    for event in pool:
        if len(board.market) >= config.VIEW_CHECK_MARKET_SIGNALS:
            break
        group = assign_brief_group(event.get("tags") or [])
        if group is None or str(event.get("id")) in seen or per_group.get(group["key"], 0) >= _MARKET_PER_GROUP:
            continue
        annotation = annotations.get(str(event.get("id")))
        if (row := add(event, annotation[2] if annotation and annotation[2] else None)) is not None:
            board.market.append(row)
            per_group[group["key"]] = per_group.get(group["key"], 0) + 1
    return board


def judge_payload(claims: list[Claim], board: Board) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """판정 단계의 모델 입력. 확률 숫자는 없고 등급만 있다(`build_row`)."""
    return ([{"c": index, "claim": claim.text, "candidates": [row.model for row in rows]}
             for index, (claim, rows) in enumerate(zip(claims, board.by_claim), start=1)],
            [row.model for row in board.market])


def _signal(row: Row, focus: list[int]) -> dict[str, Any]:
    """화면에 세울 질문 하나. 선택지와 확률은 detail 값 그대로다."""
    event = row.event
    return {
        "id": str(event.get("id")), "title": str(event.get("title") or ""), "summary": row.summary,
        "category_label": event.get("category_label"), "event_type": event.get("event_type"),
        "end_date": event.get("end_date"), "volume24hr": event.get("volume24hr"),
        "options": shown_options(event, row.markets, {row.options[m] for m in focus if m in row.options}),
    }


def claims_report(claims: list[Claim], judgement: Judgement, board: Board) -> list[dict[str, Any]]:
    return [{
        "c": verdict.c, "claim": claim.text, "queries": claim.queries, "verdict": verdict.verdict,
        "note": verdict.note, "signals": [_signal(board.rows[pick.n], pick.focus) for pick in verdict.picks],
    } for claim, verdict in zip(claims, judgement.verdicts)]


def blind_spots_report(judgement: Judgement, board: Board) -> list[dict[str, Any]]:
    return [{"note": note, "signal": _signal(board.rows[n], [])} for n, note in judgement.blind_spots]


def _consensus_line(signal: dict[str, Any]) -> list[dict[str, str]]:
    """포지션 단계 모델에 보이는 꼴: 질문, 선택지, 등급. 짚은 선택지가 있으면 그것만, 없으면 가장 높은 것."""
    options = [option for option in signal["options"] if option["focus"]] or signal["options"][:1]
    return [{"question": signal["summary"] or signal["title"], "option": option["label"],
             "outlook": outlook(option["probability"])} for option in options]


def positions_payload(view: str, claims: list[dict[str, Any]], blind_spots: list[dict[str, Any]],
                      watchlist: dict[str, str]) -> dict[str, Any]:
    stocks = [{"code": code, "name": name} for code, name in watchlist.items() if code not in BY_CODE]
    return {
        "view": view,
        "claims": [{"c": claim["c"], "claim": claim["claim"], "verdict": VERDICT_LABEL[claim["verdict"]],
                    "consensus": [line for signal in claim["signals"] for line in _consensus_line(signal)]}
                   for claim in claims],
        "signals": [{**line, "note": spot["note"]} for spot in blind_spots for line in _consensus_line(spot["signal"])],
        "themes": for_model(),
        "stocks": stocks[: config.VIEW_CHECK_MAX_STOCKS],
    }


def position_codes(watchlist: dict[str, str]) -> set[str]:
    return set(BY_CODE) | set(list(watchlist)[: config.VIEW_CHECK_MAX_STOCKS])


def positions_report(positions: list[Position], claims: list[dict[str, Any]],
                     watchlist: dict[str, str]) -> list[dict[str, Any]]:
    """상품 이름·성격·주의 문구를 붙인다. 근거 전제가 전부 어긋난 확대·축소는 지켜보기로 내린다."""
    verdicts = {claim["c"]: claim["verdict"] for claim in claims}
    report = []
    themes: set[str] = set()
    for position in positions:
        instrument = BY_CODE.get(position.code)
        # 한 주제에서는 아이디어 하나만 낸다. 모델이 같은 이유를 상품만 바꿔 되풀이한다(2026-11-11 실측:
        # 반도체 셋·전력 둘이 한 문장으로 나왔다).
        if instrument is not None:
            if THEME_OF[position.code].key in themes:
                continue
            themes.add(THEME_OF[position.code].key)
        linked = [verdicts[c] for c in position.claims]
        stance, cautions = position.stance, []
        if stance in ("long", "reduce"):
            if all(verdict == "contradicted" for verdict in linked):
                stance = "wait"
                cautions.append(CAUTION_DOWNGRADED)
            elif "supported" not in linked:
                cautions.append(CAUTION_UNSUPPORTED)
        if instrument is not None and (instrument.side < 0 or instrument.leverage != 1):
            cautions.append(CAUTION_GEARED)
        if instrument is not None and instrument.kind == "ETN":
            cautions.append(CAUTION_ETN)
        # 같은 주제·같은 방향·같은 배수의 다른 상품. 모델은 주제마다 하나만 고르고, 한국 상장과 미국 상장 가운데
        # 무엇을 쓸지는 계좌 사정이라 서버가 표에서 나란히 보인다.
        alternatives = [] if instrument is None else [
            {"code": other.code, "name": other.name, "market": other.market, "kind": other.kind}
            for other in THEME_OF[position.code].instruments
            if other.code != instrument.code and (other.side, other.leverage) == (instrument.side, instrument.leverage)]
        report.append({
            "code": position.code,
            "name": instrument.name if instrument else watchlist.get(position.code, position.code),
            "kind": instrument.kind if instrument else "주식",
            "market": instrument.market if instrument else None,
            "theme": THEME_OF[position.code].label if instrument else None,
            "direction": describe(instrument) if instrument else None,
            "tracks": instrument.note if instrument else None,
            "stance": stance, "claims": position.claims, "reason": position.reason,
            "invalidation": position.invalidation, "cautions": cautions, "alternatives": alternatives,
        })
    return report


def drift(repository: PolymarketRepository, report: dict[str, Any]) -> dict[str, Any]:
    """검증 때 보인 선택지의 지금 확률. 질문이 닫혔거나 읽지 못하면 그 질문은 None이다.

    화면이 검증 때 값과 빼서 "그 뒤 얼마나 움직였나"를 보인다. LLM을 부르지 않는다.
    """
    manifest = repository.load()
    signals = [signal for claim in report.get("claims", []) for signal in claim.get("signals", [])]
    signals += [spot["signal"] for spot in report.get("blind_spots", []) if spot.get("signal")]
    current: dict[str, dict[str, float] | None] = {}
    for signal in signals:
        key = str(signal.get("id"))
        if key in current:
            continue
        try:
            detail = repository.detail(key)
            markets = priced_markets(detail) if detail else []
            current[key] = option_probabilities(detail, markets) if markets else None
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            current[key] = None
    return {"generation_id": manifest.get("generation_id"), "generated_at": manifest.get("generated_at"),
            "alert": config.VIEW_CHECK_DRIFT_ALERT, "now": current}
