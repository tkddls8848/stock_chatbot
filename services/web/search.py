"""공개 산출물 검색. 국가·기간·논조·주제 표현을 해석하고 외부 호출 없이 찾는다.

자유로운 질문에 답을 생성하는 챗봇이 아니다. 해석한 조건과 원문을 함께 반환해
검색 범위를 드러낸다. 리서치·봇 내부 상태에는 접근하지 않는다.
"""

from __future__ import annotations

import json
import math
import re
import threading
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from services.web.core.clock import today

MARKETS = {"CN": "중국", "HK": "홍콩", "US": "미국", "KR": "한국", "JP": "일본"}
_COUNTRIES = {
    "CN": ("중국", "중국 본토", "중화권", "china", "chinese", "a주"),
    "HK": ("홍콩", "hong kong", "hang seng", "항셍"),
    "US": ("미국", "미 증시", "united states", "usa", "미장"),
    "KR": ("한국", "국내", "우리나라", "korea", "korean", "국장"),
    "JP": ("일본", "일 증시", "japan", "japanese", "닛케이", "니케이"),
}
# 검색 도메인의 한·영·일·중 표기. 개별 기사마다 번역·주석 호출을 추가하지 않는다.
_TOPICS = {
    "금리": ("금리", "기준금리", "interest rate", "rate hike", "rate cut", "利率", "金利", "利上げ", "利下げ"),
    "인상": ("인상", "올리는", "긴축", "hike", "tightening", "加息", "利上げ"),
    "인하": ("인하", "내리는", "완화", "rate cut", "easing", "降息", "利下げ"),
    "반도체": ("반도체", "semiconductor", "chip", "半導体", "半导体"),
    "인플레이션": ("인플레이션", "물가", "소비자물가", "inflation", "cpi", "インフレ", "通胀"),
    "관세": ("관세", "tariff", "関税", "关税"),
    "환율": ("환율", "환시", "exchange rate", "forex", "為替", "汇率"),
    "엔화": ("엔화", "엔저", "엔고", "yen", "円安", "円高", "日元"),
    "유가": ("유가", "원유", "국제유가", "crude", "oil", "原油", "油价"),
    "실적": ("실적", "영업이익", "순이익", "earnings", "profit", "決算", "业绩"),
    "경기침체": ("경기침체", "경기 침체", "불황", "recession", "景気後退", "衰退"),
    "연준": ("연준", "연방준비제도", "fomc", "federal reserve", "fed", "美联储"),
    "일본은행": ("일본은행", "일본 은행", "boj", "bank of japan", "日銀", "日本銀行"),
    "인공지능": ("인공지능", "ai", "artificial intelligence", "人工智能", "生成ai"),
    "비트코인": ("비트코인", "bitcoin", "btc", "ビットコイン", "比特币"),
    "부동산": ("부동산", "real estate", "property", "不動産", "房地产"),
    "중앙은행": ("중앙은행", "central bank", "中央银行"),
}
_STOP = set("뉴스 기사 시장 증시 상황 감성 관련 대한 대해 영향 소식 자료 내용 최근 최신 좀 것 중 모든 전체 찾아 찾아줘 찾아주세요 알려줘 알려주세요 보여줘 보여주세요 검색 검색해줘 정리 요약 어떻게 어때 어떤 무슨 있는 있나요 부탁해 및 그리고 와 과 에서 에 대한 것들 비교 더 많이 왜 이유 줘 주세요 미친 미치는 미칠 부터 까지 동안 간".split())
_SUFFIX = re.compile(r"(?:에서는|에서|으로|에게|에는|하고|이랑|관련|처럼|까지|부터|보다|이나|이나요|인가요|은|는|이|가|을|를|에|의|과|와|도|만)$")


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _pattern(alias: str) -> str:
    # 영문 'ai'가 'said'에 걸리지 않게 단어 경계를 둔다. 한국어 조사는 허용한다.
    escaped = re.escape(alias).replace(r"\ ", r"\s*")
    if re.fullmatch(r"[a-z0-9 ]+", alias):
        return r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])"
    return escaped


_TOPIC_PATTERNS = {
    label: re.compile("|".join(_pattern(v) for v in sorted(aliases, key=len, reverse=True)))
    for label, aliases in _TOPICS.items()
}
_COUNTRY_PATTERNS = {
    code: re.compile("|".join(_pattern(v) for v in sorted(aliases, key=len, reverse=True)))
    for code, aliases in _COUNTRIES.items()
}


def interpret(query: str, *, market: str = "", days: int | None = None) -> dict:
    current = today()
    start, end = current - timedelta(days=29), current
    text = _normal(query)
    topics = []
    # 일본은행은 기관명이다. 먼저 소비해 일본 필터로 잘못 잘라내지 않는다.
    for label in ("일본은행",):
        pattern = _TOPIC_PATTERNS[label]
        if pattern.search(text):
            topics.append(label)
            text = pattern.sub(" ", text)
    markets = []
    for code, pattern in _COUNTRY_PATTERNS.items():
        if pattern.search(text):
            markets.append(code)
            text = pattern.sub(" ", text)
    period = re.search(r"(?:최근|지난)?\s*(\d{1,3})\s*일(?:간|동안)?", text)
    explicit = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if explicit:
        try:
            dates = [date.fromisoformat(value) for value in explicit[:2]]
        except ValueError as exc:
            raise ValueError("날짜를 YYYY-MM-DD 형식의 실제 날짜로 입력해 주세요.") from exc
        start, end = min(dates), max(dates)
        text = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text)
    elif "그저께" in text:
        start = end = current - timedelta(days=2)
        text = text.replace("그저께", " ")
    elif "어제" in text:
        start = end = current - timedelta(days=1)
        text = text.replace("어제", " ")
    elif "오늘" in text:
        start = end = current
        text = text.replace("오늘", " ")
    elif re.search(r"지난\s*주(?!식)", text):
        end = current - timedelta(days=current.weekday() + 1)
        start = end - timedelta(days=6)
        text = re.sub(r"지난\s*주", " ", text)
    elif re.search(r"이번\s*주(?!식)", text):
        start = current - timedelta(days=current.weekday())
        text = re.sub(r"이번\s*주", " ", text)
    elif re.search(r"(?:최근\s*)?(?:일주일|한\s*주)(?:간|동안)?", text):
        start = current - timedelta(days=6)
        text = re.sub(r"(?:최근\s*)?(?:일주일|한\s*주)(?:간|동안)?", " ", text)
    elif re.search(r"이번\s*달", text):
        start = current.replace(day=1)
        text = re.sub(r"이번\s*달", " ", text)
    elif period:
        count = int(period[1])
        if not 1 <= count <= 30:
            raise ValueError("검색 기간은 최근 1~30일을 지원합니다.")
        start = current - timedelta(days=count - 1)
        text = text[:period.start()] + " " + text[period.end():]
    if days is not None:
        start, end = current - timedelta(days=days - 1), current
    sentiment = None
    positive = re.search(r"긍정|호재", text)
    negative = re.search(r"부정|악재", text)
    if bool(positive) != bool(negative):
        sentiment = "positive" if positive else "negative"
    text = re.sub(r"긍정적|부정적|긍정|부정|호재|악재", " ", text)
    for label, pattern in _TOPIC_PATTERNS.items():
        if pattern.search(text):
            topics.append(label)
    # 겹치는 주제(금리 인하)는 먼저 모두 인식한 뒤 소비한다.
    for pattern in _TOPIC_PATTERNS.values():
        text = pattern.sub(" ", text)
    keywords = []
    for token in re.findall(r"[a-z0-9가-힣一-龥ぁ-んァ-ヶ]+", text):
        if token in _STOP:
            continue
        stripped = _SUFFIX.sub("", token)
        token = stripped if len(stripped) >= 2 else token
        if len(token) >= 2 and token not in _STOP and token not in keywords:
            keywords.append(token)
    return {
        "markets": [market] if market else markets,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "sentiment": sentiment, "topics": list(dict.fromkeys(topics)), "keywords": keywords,
    }


def _safe_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        return value if parts.scheme in {"http", "https"} and parts.hostname and not parts.username else ""
    except ValueError:
        return ""


def _prepare(row: dict) -> dict:
    public = {key: row.get(key) for key in (
        "id", "kind", "market", "title", "text", "date", "published_at", "source", "sentiment",
    )}
    public["url"] = _safe_url(str(row.get("url") or ""))
    public["market_label"] = MARKETS.get(row.get("market"), row.get("market", ""))
    searchable = _normal(f"{row.get('title', '')} {row.get('text', '')}")
    return {"public": public, "text": searchable,
            "topics": {label for label, pattern in _TOPIC_PATTERNS.items() if pattern.search(searchable)}}


class NewsSearch:
    """파일 변경 때만 다시 읽는다. 하나의 객체를 웹 프로세스가 재사용한다."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        self._cache: dict = {}

    def _load(self, name: str) -> tuple[list[dict], str]:
        path = self.root / name
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if name in self._cache and self._cache[name][0] == signature:
                return self._cache[name][1:]
            payload = json.loads(path.read_text(encoding="utf-8"))
            if name == "news.json":
                rows = payload.get("documents", [])
            else:
                rows = []
                for market, value in payload.get("markets", {}).items():
                    for day in value.get("daily", []):
                        if not day.get("summary"):
                            continue
                        rows.append({
                            "id": f"market:{market}:{day['date']}", "kind": "market", "market": market,
                            "title": f"{MARKETS.get(market, market)} 일일 시장 요약",
                            "text": day["summary"], "date": day["date"], "published_at": "",
                            "source": "눈치 국가별 뉴스 감성", "sentiment": day.get("avg_sentiment"),
                        })
            docs = [_prepare(row) for row in rows if isinstance(row, dict) and row.get("date")]
            self._cache[name] = (signature, docs, str(payload.get("generated_at") or ""))
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            # 일시적 읽기 오류에는 마지막 정상 자료만 사용한다. 검색 시 날짜를 다시 거른다.
            pass
        return self._cache.get(name, (None, [], ""))[1:]

    def search(self, query: str, *, market: str = "", days: int | None = None,
               page: int = 1, page_size: int = 20) -> dict:
        filters = interpret(query, market=market, days=days)
        with self._lock:
            news, news_at = self._load("news.json")
            markets, market_at = self._load("market.json")
        docs = news + markets
        cutoff = (today() - timedelta(days=29)).isoformat()
        current = today().isoformat()
        results = []
        for doc in docs:
            row = doc["public"]
            if not cutoff <= row["date"] <= current:
                continue
            if not filters["start_date"] <= row["date"] <= filters["end_date"]:
                continue
            if filters["markets"] and row["market"] not in filters["markets"]:
                continue
            s = row["sentiment"]
            if filters["sentiment"]:
                if not isinstance(s, (int, float)) or not math.isfinite(s):
                    continue
                if (filters["sentiment"] == "positive" and s <= 0) or (filters["sentiment"] == "negative" and s >= 0):
                    continue
            if not set(filters["topics"]).issubset(doc["topics"]):
                continue
            if not all(re.search(_pattern(token), doc["text"]) for token in filters["keywords"]):
                continue
            title = _normal(row["title"] or "")
            score = sum(bool(_TOPIC_PATTERNS[t].search(title)) for t in filters["topics"]) * 3
            score += sum(bool(re.search(_pattern(t), title)) for t in filters["keywords"]) * 3
            results.append((score, row))
        results.sort(key=lambda item: (item[0], item[1]["date"], item[1].get("published_at") or "", item[1]["id"]), reverse=True)
        total = len(results)
        start = (page - 1) * page_size
        return {
            "query": query, "filters": filters, "total": total, "page": page,
            "page_count": math.ceil(total / page_size),
            "results": [row for _, row in results[start:start + page_size]],
            "sources_updated_at": {"news": news_at, "market": market_at},
            "available_documents": sum(cutoff <= doc["public"]["date"] <= current for doc in docs),
        }
