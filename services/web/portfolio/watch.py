"""내 자산 점검 — 보유 주식·관심종목을 공개 기사·시장상황 보고서와 맞춰 보고, 규칙 경보를 모은다.

조언(`service.py`)은 요청할 때 한 번 받는 진단이고, 이것은 매일 들여다보는 자리다. 화면
(`GET /api/portfolio/watch`)과 뉴스레터(`newsletter/send.py`, 계정이 켰을 때만)가 같은 결과를 쓴다.

**LLM·외부 API를 부르지 않는다.** 읽는 것은 계정 파일(자산·관심종목)과 봇이 구운 산출물뿐이다 —
`news.json`(보고서와 그 근거 기사, 논조 있음), `watch_news.json`(보고서 큐에 들어온 기사 72시간, 하루
700~1,300건, 논조 없음), `market.json`. 숫자는 규칙 진단(`diagnosis.py`)이 만들고, 여기서는 고르고
묶기만 한다.

종목 찾기는 이름·코드가 기사 제목·원제에 나올 때 맞춘다. 주요 종목은 여러 언어 이름(`aliases.py`)으로도
찾는다 — "삼성전자"로 적어도 "Samsung Electronics"·"三星电子"를 찾는다. 표에 없는 종목은 적힌 그대로만
찾고, 그 범위를 화면에 밝힌다.
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from services.web.core.clock import ensure_jst
from services.web.portfolio.aliases import lookup
from services.web.portfolio.diagnosis import diagnose
from services.web.search import MARKETS

# 이름 끝의 회사 형태. 사용자가 "Alphabet Inc."·"Data I/O Corporation - Common Stock"처럼 적어도
# 기사에는 "Alphabet"·"Data I/O"로 나온다.
_SUFFIX = re.compile(
    r"(?:\s*-\s*common stock|\s*,?\s*(?:inc|corp|corporation|co|company|ltd|limited|plc|holdings?|group)\.?"
    r"|\s*(?:주식회사|\(주\)|보통주))+$"
)
_PREFIX = re.compile(r"^(?:주식회사|\(주\))\s*")
_US_TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")
# 영문 이름은 이보다 짧으면 다른 낱말에 걸린다("SK"가 "risk"에). 한·중·일 이름은 두 글자부터 찾는다.
_MIN_LATIN = 3
# 거래소 표기 없이 맨몸으로 찾는 티커의 최소 길이. "MU"·"ON"은 낱말과 구분할 수 없다.
_MIN_BARE_TICKER = 4
# 두 글자 한국어 이름은 다른 낱말 속에 든다("애플"→애플리케이션, "기아"→위기아). 앞이 한글이 아니고, 뒤가
# 한글이 아니거나 조사일 때만 맞춘다. 세 글자부터는 그대로 찾는다 — "삼성전자에서"처럼 조사가 길게 붙는다.
_SHORT_HANGUL = 2
_PARTICLE = "이가은는을를의에와과도만로서"
# 버전 형식. 봇의 `publish.WATCH_NEWS_FORMAT`과 같아야 읽는다.
WATCH_NEWS_FORMAT = 1


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _alias(name: str) -> str:
    value = _PREFIX.sub("", _normal(name))
    return _SUFFIX.sub("", value).strip(" .,-")


def _code_market(code: str) -> str:
    """관심종목 정규 코드(`store.canonical_code`)의 시장. CN·HK는 접두사 없는 숫자다."""
    if code.startswith(("KR:", "US:")):
        return code[:2]
    if code.isdigit():
        return "CN" if len(code) == 6 else "HK" if len(code) == 5 else ""
    return ""


def _patterns(name: str, market: str, code: str) -> list[tuple[re.Pattern[str], bool]]:
    """그 종목을 기사에서 찾는 (정규식, 원문에서 찾나). 이름은 정규화한(소문자) 글에서 찾고,
    코드는 원문에서 찾는다 — 티커는 대문자여야 낱말과 구분된다."""
    found: list[tuple[re.Pattern[str], bool]] = []
    written = {_alias(name), _normal(name)}
    for alias in sorted({_alias(n) for n in (*written, *lookup(market, code, written))} - {""}):
        escaped = re.escape(alias).replace(r"\ ", r"\s*")
        if re.fullmatch(r"[a-z0-9&.'/ -]+", alias):
            if len(alias.replace(" ", "")) >= _MIN_LATIN:
                found.append((re.compile(r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])"), False))
        elif re.fullmatch(r"[가-힣]+", alias) and len(alias) <= _SHORT_HANGUL:
            found.append((re.compile(rf"(?<![가-힣]){escaped}(?:(?![가-힣])|(?=[{_PARTICLE}]))"), False))
        elif len(alias) >= 2:
            found.append((re.compile(escaped), False))  # 한국어 조사가 붙어도 찾는다
    raw = code.rsplit(":", 1)[-1].strip().upper()
    if market == "US" and _US_TICKER.fullmatch(raw) and not raw.isdigit():
        ticker = re.escape(raw)
        explicit = rf"(?:\$|\((?:[A-Z]+:\s?)?|(?:NASDAQ|NYSE):\s?){ticker}(?![A-Za-z0-9])"
        bare = rf"|(?<![A-Za-z0-9$]){ticker}(?![A-Za-z0-9])" if len(raw) >= _MIN_BARE_TICKER else ""
        found.append((re.compile(explicit + bare), True))
    elif market in {"KR", "CN"} and raw.isdigit() and len(raw) <= 6:
        found.append((re.compile(rf"(?<!\d){raw.zfill(6)}(?!\d)"), True))
    elif market == "HK" and raw.isdigit() and len(raw) <= 5:
        found.append((re.compile(rf"(?<!\d){raw.zfill(5)}(?!\d)|(?<![\d.]){int(raw)}\.HK\b"), True))
    return found


def holdings(assets: list[dict[str, Any]], watchlist: dict[str, str]) -> list[dict[str, Any]]:
    """찾을 종목 목록. 보유 주식이 먼저이고, 같은 시장·이름의 관심종목은 한 번만 둔다."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    candidates = [(str(a.get("name") or ""), str(a.get("market") or ""), str(a.get("code") or ""), True)
                  for a in assets if a.get("kind") == "stock"]
    candidates += [(name, _code_market(code), code, False) for code, name in watchlist.items()]
    for name, market, code, held in candidates:
        identity = (market, _alias(name) or code)
        if not name.strip() or identity in seen:
            continue
        seen.add(identity)
        rows.append({"name": name.strip(), "market": market, "market_label": MARKETS.get(market, market),
                     "code": code, "held": held})
    return rows


def _stamp(value: Any) -> datetime | None:
    try:
        return ensure_jst(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _public(row: dict[str, Any]) -> dict[str, Any]:
    url = str(row.get("url") or "")
    return {
        "kind": row.get("kind"), "market": row.get("market"), "title": str(row.get("title") or ""),
        "source": str(row.get("source") or ""), "published_at": str(row.get("published_at") or ""),
        "url": url if url.startswith(("https://", "http://")) else "",
        "sentiment": row.get("sentiment") if isinstance(row.get("sentiment"), (int, float)) else None,
    }


def _lead(text: str) -> str:
    return next((part.strip() for part in str(text).split("\n") if part.strip()), "")


class PublicFeed:
    """공개 산출물 두 파일. 웹 프로세스는 한 객체를 재사용하고 파일이 바뀔 때만 다시 읽는다."""

    def __init__(self, public_dir: Path):
        self._dir = public_dir
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}

    def read(self, name: str) -> dict[str, Any]:
        path = self._dir / name
        with self._lock:
            try:
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                cached = self._cache.get(name)
                if cached and cached[0] == signature:
                    return cached[1]
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}  # 없거나 깨졌으면 "자료 없음"이다. 결과의 `data`에 드러난다
            value = value if isinstance(value, dict) else {}
            self._cache[name] = (signature, value)
            return value


def build_watch(assets: list[dict[str, Any]], watchlist: dict[str, str], feed: PublicFeed,
                moment: datetime, *, news_hours: int, report_hours: int, matches: int) -> dict[str, Any]:
    since = moment - timedelta(hours=news_hours)
    news = feed.read("news.json")
    queued = feed.read("watch_news.json")
    if queued.get("format") != WATCH_NEWS_FORMAT:
        queued = {}  # 형식이 다르면 "자료 없음"이다(현재 형식만 읽는다)
    # 보고서 근거(news.json)가 먼저다 — 한국어 제목과 논조가 있다. 같은 기사가 큐 묶음에도 있으면 뺀다.
    rows = [row for row in news.get("documents", []) if isinstance(row, dict) and row.get("kind") in {"news", "report"}]
    rows += [{**row, "kind": "news", "text": ""} for row in queued.get("items", []) if isinstance(row, dict)]
    documents, seen = [], set()
    for row in rows:
        stamp = _stamp(row.get("published_at"))
        if stamp is None or not since <= stamp <= moment:
            continue
        original = f"{row.get('title', '')} {row.get('text', '')}"
        # news.json의 `text`가 원제이고, 큐 묶음은 `title`이 원제다. 주소나 원제 중 하나가 같으면 같은 기사다.
        keys = {key for key in (str(row.get("url") or ""), _normal(str(row.get("text") or row.get("title") or "")))
                if key}
        if row.get("kind") == "news" and keys & seen:
            continue
        seen |= keys
        documents.append((stamp, row, _normal(original), original))
    documents.sort(key=lambda item: item[0], reverse=True)

    tracked = holdings(assets, watchlist)
    for row in tracked:
        patterns = _patterns(row["name"], row["market"], row["code"])
        hits = [doc for _, doc, folded, original in documents
                if any(pattern.search(original if on_original else folded) for pattern, on_original in patterns)]
        tones = [d["sentiment"] for d in hits if d.get("kind") == "news" and isinstance(d.get("sentiment"), (int, float))]
        row.update({"count": len(hits), "matches": [_public(d) for d in hits[:matches]],
                    "tone": round(sum(tones) / len(tones), 2) if tones else None})
    tracked.sort(key=lambda r: (not r["held"], -r["count"]))

    market = feed.read("market.json")
    market_rows = market.get("markets") if isinstance(market.get("markets"), dict) else {}
    report_since = moment - timedelta(hours=report_hours)
    contexts = []
    for code in [c for c in MARKETS if any(r["market"] == c for r in tracked)]:
        daily = sorted((p for p in (market_rows.get(code) or {}).get("daily", []) if isinstance(p, dict)),
                       key=lambda p: str(p.get("date", "")))
        last = daily[-1] if daily else None
        report = next((doc for stamp, doc, _, _ in documents
                       if doc.get("kind") == "report" and doc.get("market") == code and stamp >= report_since), None)
        contexts.append({
            "code": code, "name": MARKETS[code],
            "tone": float(last["avg_sentiment"]) if last and last.get("avg_sentiment") is not None else None,
            "tone_date": str(last.get("date", "")) if last else "",
            "summary": str(last.get("summary") or "") if last else "",
            "report": {**_public(report), "lead": _lead(str(report.get("text") or ""))} if report else None,
        })

    report = diagnose(assets, today=moment.date(), deposit_rates={}, market_rates={}, estimates={})
    alerts = [{key: f[key] for key in ("level", "code", "message", "key", "brief", "asset_id") if key in f}
              for f in report["findings"] if f.get("key")]
    return {
        "as_of": moment.isoformat(timespec="seconds"),
        "news_hours": news_hours,
        "alerts": alerts,
        "holdings": tracked,
        "markets": contexts,
        "data": {"news_generated_at": str(news.get("generated_at") or ""),
                 "watch_news_generated_at": str(queued.get("generated_at") or ""),
                 "market_generated_at": str(market.get("generated_at") or ""),
                 "documents": len(documents)},
    }
