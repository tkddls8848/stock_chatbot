"""추적 코너: 추적 주제의 확률 기록을 영상 장면의 재료로 바꾼다. 계획: `docs/watch-topics.md` 4절.

`watch.py`가 고른 주제(`topics.json`)와 그 일별 기록(`history/`)을 읽어, 주제마다 "지금 몇 %이고 지난 30일·어제와
견줘 어떻게 움직였는가"를 낸다. 숫자와 기간은 전부 여기서 정하고, 모델은 이름과 맥락(제목·질문·선택지 이름·전망
구절·해설)만 쓴다. 그 글은 주제가 바뀌지 않는 한 같아야 하므로 처음 한 번만 쓰고 `topics.json`에 넣어 둔다 — 매일
다시 쓰면 같은 판의 이름이 날마다 달라지고 모델 호출도 는다.

코너는 영상 제작을 막지 않는다. 기록이 모자라거나 글을 쓰지 못한 주제는 그날만 빠지고 사유가 감사 기록에 남는다.
"""

from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

from .config import Settings
from .core.storage import read_json, write_json
from .highlights import HighlightError, write_issues
from .llm import LLMError
from .watch import OPEN_HIGH, OPEN_LOW, history_path, ledger_dir, pinned_topics, watch_dir

logger = logging.getLogger(__name__)

# 추이선에 담는 기간과, 선을 그리려면 있어야 하는 기록 일수. 이보다 적으면 선이 점 몇 개라 흐름으로 읽히지 않는다.
WINDOW_DAYS = 30
MIN_POINTS = 5
# 마지막으로 길게(해설까지) 다룬 뒤 확률이 이만큼 움직이면 다시 길게 다룬다. 그 밖의 날은 숫자만 짧게 잇는다.
# 긴 설명은 하루에 한 주제만 한다 — 두 주제를 같은 날 길게 읽은 시험 영상이 203초였다(2026-11-10, 목표 150초).
DEEP_MOVE = .10
# 단일 답 묶음은 1위가 바뀔 수 있어 상위 몇 개의 이름을 함께 받아 둔다.
SCRIPT_MARKETS = 3
# 추이선 카드의 답 이름 한 줄에 드는 글자 수. 넘는 이름을 저장해 두면 매일 렌더가 실패한다(화면은 글자를 버리지 않는다).
MAX_LABEL_CHARS = 24
# 원고 분량을 나누는 가상의 이슈 수(`highlights.write_issues`의 `total`). 코너 글은 이슈 하나보다 짧게 쓴다.
_SCRIPT_SHARE = 4


def _focus(slot: dict[str, Any], markets: list[dict[str, Any]]) -> dict[str, Any]:
    """추이선으로 그릴 개별 질문.

    - 단일 답 묶음: 오늘 확률이 가장 높은 답.
    - 기한·기준 묶음: 한 번 정한 칸을 그 칸이 열려 있는 동안(5~95%) 유지한다. 날마다 50%에 가까운 칸으로 옮기면
      선이 다른 질문으로 바뀌어 어제와 견줄 수 없다. 칸이 닫히면 50%에 가장 가까운 칸으로 옮긴다.
    - 양자택일: 하나뿐인 질문.
    """
    if slot.get("event_type") == "exclusive_multi":
        return max(markets, key=lambda market: (market["yes"], market["id"]))
    kept = next((market for market in markets if market["id"] == slot.get("focus")), None)
    if kept and (len(markets) == 1 or OPEN_LOW <= kept["yes"] <= OPEN_HIGH):
        return kept
    return min(markets, key=lambda market: (abs(market["yes"] - .5), market["id"]))


def trend(points: dict[str, float], today: date) -> dict[str, Any] | None:
    """한 질문의 지난 `WINDOW_DAYS`일 기록에서 견줄 값을 뽑는다. 오늘 값이 없거나 기록이 모자라면 None.

    어제 값은 어제 날짜의 기록이 있을 때만 준다 — 하루를 건너뛴 기록을 "어제"라고 부르지 않는다.
    """
    window = [(day, value) for day, value in sorted(points.items())
              if today - timedelta(days=WINDOW_DAYS) <= date.fromisoformat(day) <= today]
    if len(window) < MIN_POINTS or window[-1][0] != today.isoformat():
        return None
    return {
        "points": window, "now": window[-1][1],
        "base": window[0][1], "base_days": (today - date.fromisoformat(window[0][0])).days,
        "yesterday": points.get((today - timedelta(days=1)).isoformat()),
        "week": points.get((today - timedelta(days=7)).isoformat()),
    }


def _previous_leader(lines: dict[str, Any], focus_id: str, today: date) -> str | None:
    """어제 확률이 가장 높던 답이 오늘의 1위와 다르면 그 답의 ID."""
    yesterday = (today - timedelta(days=1)).isoformat()
    ranked = [(line["points"][yesterday], market_id) for market_id, line in lines.items()
              if yesterday in line.get("points", {})]
    if focus_id not in {market_id for _, market_id in ranked}:
        return None
    leader = max(ranked)[1]
    return leader if leader != focus_id else None


def _write_script(slot: dict[str, Any], markets: list[dict[str, Any]], settings: Settings, today: date) -> dict:
    """제목·질문·선택지 이름·전망 구절·해설을 일일 원고와 같은 모델·같은 검증으로 쓴다."""
    issue = {"id": slot["event_id"], "title": slot["title"], "description": "",
             "markets": [{"id": market["id"], "question": market["question"]} for market in markets],
             "news": [], "market_news": []}
    script = write_issues([issue], settings, total=_SCRIPT_SHARE)[0]
    return {"headline": script["headline"], "question": script["question"], "context": script["context"],
            "labels": {label["id"]: {key: label[key] for key in ("label", "outlook") if key in label}
                       for label in script["market_labels"]},
            "written_on": today.isoformat()}


def build(settings: Settings, today: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """오늘 영상에 넣을 추적 주제의 재료와 감사 기록.

    `watch.refresh_watch`가 오늘 기록과 주제를 고친 뒤에 부른다. 고른 질문·쓴 글·긴 설명을 한 날은 `topics.json`의
    그 주제에 남긴다.
    """
    path = watch_dir(settings) / "topics.json"
    state = read_json(path)
    ledger = read_json(ledger_dir(settings) / f"{today.isoformat()}.json")
    events = {row["id"]: row for row in ledger.get("events") or []}
    views: list[dict[str, Any]] = []
    audit: dict[str, Any] = {"llm_calls": 0, "skipped": []}
    # 오늘 이미 길게 다룬 주제가 있으면(같은 날 다시 제작) 그 주제가 오늘의 긴 설명이다.
    long_taken = any((slot.get("deep") or {}).get("date") == today.isoformat() for slot in state.get("tracked") or [])
    for slot in state.get("tracked") or []:
        def skip(reason: str, slot=slot) -> None:
            audit["skipped"].append({"event_id": slot["event_id"], "reason": reason})

        row = events.get(slot["event_id"])
        markets = [market for market in (ledger.get("details") or {}).get(slot["event_id"]) or []
                   if market.get("question")]
        if row is None or not markets:
            skip("오늘 기록에 개별 질문이 없음")
            continue
        focus = _focus(slot, markets)
        lines = read_json(history_path(settings, slot["event_id"])).get("markets") or {}
        measured = trend((lines.get(focus["id"]) or {}).get("points") or {}, today)
        if measured is None:
            skip("지난 기록이 모자람")
            continue
        script = slot.get("script") or {}
        if focus["id"] not in script.get("labels", {}):
            chosen = (sorted(markets, key=lambda market: -market["yes"])[:SCRIPT_MARKETS]
                      if slot.get("event_type") == "exclusive_multi" else [focus])
            audit["llm_calls"] += 1
            try:
                script = _write_script(slot, chosen, settings, today)
            except (HighlightError, LLMError) as exc:
                logger.warning("추적 주제 %s의 글을 쓰지 못했습니다: %s", slot["event_id"], exc)
                skip(f"글을 쓰지 못함: {str(exc)[:200]}")
                continue
            if focus["id"] not in script["labels"]:
                skip("고른 질문의 이름이 검증에서 빠짐")
                continue
            if len(script["labels"][focus["id"]]["label"]) > MAX_LABEL_CHARS:
                skip("고른 질문의 이름이 화면 한 줄을 넘음")
                continue
            slot["script"] = script
        slot["focus"] = focus["id"]
        deep = slot.get("deep") or {}
        mine = deep.get("date") == today.isoformat()
        wanted = not deep or abs(measured["now"] - deep.get("value", measured["now"])) >= DEEP_MOVE
        # 차례를 얻지 못한 주제는 기록을 남기지 않아 다음 날 길게 다룬다.
        long_form = mine or wanted and not long_taken
        # 처음 소개하는 날인지도 함께 남긴다 — 같은 날 다시 만들어도 같은 말로 연다.
        first_time = long_form and (deep.get("first", False) if mine else not deep)
        if long_form:
            long_taken = True
            slot["deep"] = {"date": today.isoformat(), "value": measured["now"], "first": first_time}
        previous = (_previous_leader(lines, focus["id"], today)
                    if slot.get("event_type") == "exclusive_multi" else None)
        views.append({
            "event_id": slot["event_id"], "topic_key": slot["topic_key"], "title": slot["title"],
            "theme": slot["theme"], "since": slot["since"], "score": slot.get("score"),
            "day": (today - date.fromisoformat(slot["since"])).days + 1, "first_time": first_time,
            "end_date": row["end_date"], "market_id": focus["id"], "market_question": focus["question"],
            "headline": script["headline"], "question": script["question"], "context": script["context"],
            **script["labels"][focus["id"]],
            "previous_leader": (script["labels"].get(previous) or {}).get("label") if previous else None,
            "long": long_form, **measured,
        })
    if state.get("tracked"):
        write_json(path, state)
    audit["scenes"] = len(views)
    # 같은 흐름(theme)으로 묶인 다른 판. 새 이슈 선정이 코너와 같은 이야기를 한 번 더 고르지 않게 한다.
    themes = {view["theme"] for view in views}
    audit["same_theme"] = sorted(topic for topic, entry in (state.get("importance") or {}).items()
                                 if entry.get("theme") in themes)
    return views, audit


def status(settings: Settings) -> dict[str, Any]:
    """텔레그램 `/shorts track`이 보이는 추적 상태. 마지막 갱신일의 기록으로 견준다."""
    state = read_json(watch_dir(settings) / "topics.json")
    pins = pinned_topics(settings)
    tracked = []
    try:
        updated = date.fromisoformat(state.get("updated_on") or "")
    except ValueError:
        updated = None
    for slot in state.get("tracked") or []:
        item = {key: slot.get(key) for key in ("event_id", "title", "theme", "since", "score")}
        item["pinned"] = slot["topic_key"] in pins
        script = slot.get("script") or {}
        item["headline"] = script.get("headline")
        line = (read_json(history_path(settings, slot["event_id"])).get("markets") or {}).get(slot.get("focus")) or {}
        item["label"] = (script.get("labels") or {}).get(slot.get("focus"), {}).get("label") or line.get("label")
        measured = trend(line.get("points") or {}, updated) if updated else None
        if measured:
            item.update({key: measured[key] for key in ("now", "base", "base_days", "yesterday")})
        tracked.append(item)
    held = {slot["event_id"] for slot in state.get("tracked") or []}
    return {"updated_on": state.get("updated_on"), "tracked": tracked, "pins": pins,
            "ranking": [{**row, "tracked": row["id"] in held} for row in state.get("ranking") or []]}
