"""시장 아노말리 백필·재현성·GDELT 감사를 단계적으로 수행한다.

기본 실행은 시장별 최근 180세션 중 아직 없는 창을 최대 80개 채운다.

    python app/market_anomaly_backfill.py
    python app/market_anomaly_backfill.py --rescore
    python app/market_anomaly_backfill.py --audit
    python app/market_anomaly_backfill.py --report

백필 파일은 라이브 파일과 분리된다. --rescore와 --audit도 기존 헤드라인 입력이
있는 창만 갱신하며, 호출 상한을 공유한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import requests

from shared.core.clock import now
from shared.core.config import (
    MARKET_ANOMALY_BACKFILL_FILE,
    MARKET_ANOMALY_BACKFILL_MAX_CALLS_PER_RUN,
    MARKET_CHART_MARKETS,
)
from telegram_bot.features.market_sentiment.overnight import capture_window
from telegram_bot.features.market_sentiment.window import recent_session_windows
from shared.llm import build_overnight_tone_analyzer
from telegram_bot.state import OvernightToneStore


def _interleave(groups: dict[str, list]) -> list[tuple[str, object]]:
    result = []
    for index in range(max((len(items) for items in groups.values()), default=0)):
        for market in sorted(groups):
            if index < len(groups[market]):
                result.append((market, groups[market][index]))
    return result


async def _collect(store, analyzer, limit: int) -> int:
    app = SimpleNamespace(
        bot_data={
            "overnight_tone_store": store,
            "overnight_tone_analyzer": analyzer,
            "market_digest_semaphore": asyncio.Semaphore(1),
        }
    )
    windows = {
        market: recent_session_windows(market, now(), 180)
        for market in MARKET_CHART_MARKETS
    }
    pending = []
    for market, window in _interleave(windows):
        if not await store.contains(market, window.price_session):
            pending.append(window)
    completed = 0
    for window in pending[:limit]:
        print(f"수집 {window.market} {window.price_session} ...", flush=True)
        try:
            completed += int(
                await capture_window(app, window, record_insufficient=True)
            )
        except Exception as exc:
            print(f"  실패: {exc}", file=sys.stderr)
    return completed


async def _rescore(store, analyzer, limit: int) -> int:
    rows = await store.entries(set(MARKET_CHART_MARKETS))
    pending = [row for row in rows if row.get("headlines") and "rescore_tone" not in row]
    completed = 0
    for row in _interleave(
        {
            market: sorted(
                (item for item in pending if item.get("market") == market),
                key=lambda item: item["price_session"],
            )[:30]
            for market in MARKET_CHART_MARKETS
        }
    )[:limit]:
        _, entry = row
        result = await asyncio.to_thread(
            analyzer.analyze,
            entry["market"],
            entry["price_session"],
            entry["sentiment_for_session"],
            entry["window_start"],
            entry["window_end"],
            entry["headlines"],
        )
        entry["rescore_tone"] = result["tone"]
        await store.put(entry)
        completed += 1
    return completed


def _gdelt_headlines(entry: dict) -> list[dict[str, str]]:
    start = datetime.fromisoformat(str(entry["window_start"])).astimezone(timezone.utc)
    end = datetime.fromisoformat(str(entry["window_end"])).astimezone(timezone.utc)
    query = {
        "KR": '("KOSPI" OR "Korea stock market")',
        "CN": '("Shanghai Composite" OR "China stock market")',
        "HK": '("Hang Seng" OR "Hong Kong stock market")',
        "US": '("S&P 500" OR "Wall Street")',
    }[entry["market"]]
    response = requests.get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": 250,
            "sort": "DateAsc",
            "startdatetime": start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        },
        timeout=(5, 30),
    )
    response.raise_for_status()
    raw = response.json()
    articles = raw.get("articles", []) if isinstance(raw, dict) else []
    result = []
    seen = set()
    for article in articles:
        title = str(article.get("title") or "").strip()
        if not title or title.casefold() in seen:
            continue
        seen.add(title.casefold())
        result.append(
            {
                "title": title,
                "source": str(article.get("domain") or "gdelt"),
                "published_at": str(article.get("seendate") or ""),
            }
        )
        if len(result) >= 40:
            break
    return result


async def _audit(store, analyzer, limit: int) -> int:
    rows = await store.entries(set(MARKET_CHART_MARKETS))
    pending = [row for row in rows if "source_audit_match" not in row]
    groups = {
        market: sorted(
            (item for item in pending if item.get("market") == market),
            key=lambda item: item["price_session"],
        )[:30]
        for market in MARKET_CHART_MARKETS
    }
    completed = 0
    for _, entry in _interleave(groups)[:limit]:
        try:
            headlines = await asyncio.to_thread(_gdelt_headlines, entry)
            if len(headlines) < 8 or len({item["source"] for item in headlines}) < 4:
                continue
            result = await asyncio.to_thread(
                analyzer.analyze,
                entry["market"],
                entry["price_session"],
                entry["sentiment_for_session"],
                entry["window_start"],
                entry["window_end"],
                headlines,
            )
            def direction(value: float) -> int:
                return 0 if abs(value) < 0.10 else 1 if value > 0 else -1

            entry["source_audit_match"] = direction(float(entry["tone"])) == direction(
                float(result["tone"])
            )
            await store.put(entry)
            completed += 1
        except Exception as exc:
            print(
                f"GDELT 감사 실패 {entry['market']} {entry['price_session']}: {exc}",
                file=sys.stderr,
            )
    return completed


async def _print_report(store) -> None:
    reports = await store.gate_report(set(MARKET_CHART_MARKETS))
    print(json.dumps(reports, ensure_ascii=False, indent=2))


async def _main(args) -> int:
    store = OvernightToneStore(MARKET_ANOMALY_BACKFILL_FILE, retention_days=400)
    if args.report:
        await _print_report(store)
        return 0
    analyzer = build_overnight_tone_analyzer()
    if args.rescore:
        count = await _rescore(store, analyzer, args.max_calls)
        print(f"재채점 {count}창 완료")
    elif args.audit:
        count = await _audit(store, analyzer, args.max_calls)
        print(f"GDELT 감사 {count}창 완료")
    else:
        count = await _collect(store, analyzer, args.max_calls)
        print(f"백필 {count}창 완료")
    await _print_report(store)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rescore", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument(
        "--max-calls",
        type=int,
        default=MARKET_ANOMALY_BACKFILL_MAX_CALLS_PER_RUN,
    )
    args = parser.parse_args()
    if sum((args.rescore, args.audit, args.report)) > 1:
        parser.error("--rescore, --audit, --report는 하나만 선택합니다")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
