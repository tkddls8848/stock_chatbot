"""게시 영상의 첫 확률을 보존하고 확정 결과만 채점한다."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
import re

import requests

from .client import GAMMA_URL
from .core.clock import now
from .core.storage import read_json, write_json

# 운영자 지시(2026-11-15): 표본이 적으면 우연이 크므로 결과 30건부터 공개한다.
MIN_PUBLIC_SETTLED = 30
BUCKETS = ("50% 미만", "50~60%", "60~70%", "70~80%", "80~90%", "90~100%")


def _path(settings):
    return settings.output_dir / "state" / "accuracy.json"


def _save(settings, records):
    write_json(_path(settings), {
        "schema_version": 1, "updated_at": now().isoformat(), "records": records,
    })


def collect(settings) -> int:
    """날짜별 마지막 게시본을 모아 보존한다. 반환값은 새 질문 수다."""
    from .youtube import revision_id

    records = read_json(_path(settings)).get("records", {})
    added = 0
    days = sorted(settings.output_dir.iterdir()) if settings.output_dir.is_dir() else []
    for day in days:
        if not day.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name):
            continue
        targets = [day, *sorted((day / "revisions").glob("*"))]
        published = []
        for target in targets:
            for identifier, entry in read_json(target / "upload.json").get("revisions", {}).items():
                if isinstance(entry, dict) and re.fullmatch(r"[\w-]+", str(entry.get("video_id") or "")):
                    published.append((identifier, entry))
        if not published:
            continue
        identifier, _ = max(published, key=lambda pair: str(pair[1].get("uploaded_at") or ""))
        target = next((p for p in targets if (record := read_json(p / "review.json"))
                       and revision_id(p, record) == identifier), None)
        if target is None:
            continue
        for scene in read_json(target / "scenario.json").get("scenes", []):
            if scene.get("kind") not in {"consensus", "watch"}:
                continue
            for market_id, option in zip(scene.get("market_ids", []), scene.get("options", [])):
                if len(option) != 3 or type(option[2]) not in (int, float) or not 0 <= option[2] <= 1:
                    continue
                key = str(market_id)
                if key not in records:
                    records[key] = {
                        "market_id": key, "event_id": scene.get("event_id", ""), "label": option[0],
                        "side": scene.get("side", "yes"), "probability": option[2],
                        "first_date": day.name, "last_date": day.name, "times": 0, "dates": [],
                    }
                    added += 1
                row = records[key]
                if day.name not in row["dates"]:
                    row["dates"].append(day.name)
                    row["last_date"] = max(row["last_date"], day.name)
                    row["times"] += 1
    _save(settings, records)
    return added


def _stamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=now().tzinfo)


def _array(value):
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("invalid binary result")
    return value


def _result(row, market, moment):
    stamp = moment.isoformat()
    if market is None:
        row.setdefault("missing_since", stamp)
        if moment - _stamp(row["missing_since"]) >= timedelta(days=14):
            row.update(void=True, void_reason="missing_14_days", settled_at=stamp)
        return
    row.pop("missing_since", None)
    if type(market.get("closed")) is not bool:
        raise ValueError("invalid closed flag")
    if not market["closed"]:
        row.pop("closed_since", None)
        return
    row.setdefault("closed_since", stamp)
    closed_at = _stamp(market.get("closedTime") or row["closed_since"])
    prices = [float(p) for p in _array(market.get("outcomePrices"))]
    labels = _array(market.get("outcomes"))
    if not all(isinstance(label, str) and label.strip() for label in labels):
        raise ValueError("invalid outcome labels")
    labels = [label.strip().lower() for label in labels]
    if labels[0] == labels[1] or not all(math.isfinite(p) and 0 <= p <= 1 for p in prices):
        raise ValueError("invalid binary result")
    if prices in ([1, 0], [0, 1]):
        yes = labels.index("yes") if set(labels) == {"yes", "no"} else 0
        row.update(outcome=int(prices[1 - yes if row["side"] == "no" else yes]), settled_at=stamp)
    elif prices == [0.5, 0.5]:
        row.update(void=True, void_reason="split_resolution", settled_at=stamp)
    elif moment - closed_at >= timedelta(days=14):
        row.update(void=True, void_reason="closed_14_days", settled_at=stamp)


def settle(settings, session=None) -> dict:
    """확인 시각이 오래된 200건을 확인한다. 실패한 묶음도 다음 순회로 미룬다."""
    records = read_json(_path(settings)).get("records", {})
    pending = sorted((r for r in records.values() if "outcome" not in r and not r.get("void")),
                     key=lambda r: r.get("checked_at", ""))[:200]
    moment = now()
    failed = 0
    client = session if session is not None else requests.Session()
    try:
        for start in range(0, len(pending), 50):
            batch = pending[start:start + 50]
            for row in batch:
                row["checked_at"] = moment.isoformat()
            try:
                response = client.get(GAMMA_URL + "markets", params=[("id", r["market_id"]) for r in batch], timeout=20)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list) or any(not isinstance(r, dict) or "id" not in r for r in payload):
                    raise ValueError("invalid markets")
                markets = {str(r["id"]): r for r in payload}
                if len(markets) != len(payload):
                    raise ValueError("duplicate markets")
                updates = []
                for row in batch:
                    updated = dict(row)
                    _result(updated, markets.get(row["market_id"]), moment)
                    updates.append(updated)
                for row in updates:
                    records[row["market_id"]] = row
            except (requests.RequestException, ValueError, TypeError, KeyError, OverflowError):
                failed += len(batch)
    finally:
        if session is None:
            client.close()
    _save(settings, records)
    return {**summary(records), "failed": failed}


def summary(records) -> dict:
    rows = list(records.values())
    settled = [r for r in rows if "outcome" in r and not r.get("void")]
    favored = [r for r in settled if r["probability"] >= 0.5]
    buckets = [{"label": label, "count": 0, "realized": 0} for label in BUCKETS]
    for row in settled:
        p = row["probability"]
        index = next((i for i, edge in enumerate((0.5, 0.6, 0.7, 0.8, 0.9)) if p < edge), 5)
        buckets[index]["count"] += 1
        buckets[index]["realized"] += row["outcome"]
    void = sum(bool(r.get("void")) for r in rows)
    return {
        "settled": len(settled), "void": void, "pending": len(rows) - len(settled) - void,
        "brier": sum((r["probability"] - r["outcome"]) ** 2 for r in settled) / len(settled) if settled else None,
        "favored_hit_rate": sum(r["outcome"] for r in favored) / len(favored) if favored else None,
        "buckets": buckets,
    }


def update(settings) -> dict:
    collect(settings)
    result = settle(settings)
    if result["settled"] >= MIN_PUBLIC_SETTLED:
        write_json(settings.public_dir / "shorts" / "accuracy.json", {
            "updated_at": read_json(_path(settings))["updated_at"],
            **{key: result[key] for key in ("settled", "void", "brier", "favored_hit_rate", "buckets")},
        })
    return result
