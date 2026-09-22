"""수집된 공개 API만 읽고, 선정 이슈에 한해 상세·뉴스를 조회한다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import html
import logging
import math
import re
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlparse
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)
PAGE_SIZE, MAX_PAGES, MAX_DETAILS, MAX_NEWS_ITEMS = 100, 100, 5, 3


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    summary: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    trending: dict[str, Any]

    @property
    def generation_id(self) -> str:
        return str(self.summary["generation_id"])


class PolymarketWebClient:
    def __init__(self, base_url: str, *, timeout: float = 20, session: Any | None = None):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.session = session or requests.Session()
        self.requests = {"summary": 0, "pages": 0, "trending": 0, "details": 0, "news": 0}

    def _get(self, path: str) -> dict[str, Any]:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(f"웹 API를 읽지 못했습니다: {url}") from exc
        if not isinstance(payload, dict):
            raise SourceError("웹 API 응답이 JSON 객체가 아닙니다")
        return payload

    def _summary(self) -> dict[str, Any]:
        self.requests["summary"] += 1
        result = self._get("api/polymarket/summary")
        state = (result.get("freshness") or {}).get("state")
        if not result.get("generation_id") or state not in {"normal", "warming_up"}:
            raise SourceError(f"대시보드 데이터가 최신 상태가 아닙니다: {state}")
        try:
            stamp = datetime.fromisoformat(result["generated_at"])
            if stamp.tzinfo is None:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError("수집 기준 시각이 없습니다") from exc
        return result

    def snapshot(self) -> Snapshot:
        summary = self._summary()
        generation = str(summary["generation_id"])
        events, seen, total = [], set(), None
        for page in range(1, MAX_PAGES + 1):
            self.requests["pages"] += 1
            payload = self._get("api/polymarket/events?" + urlencode({
                "status": "ok", "sort": "volume24hr", "order": "desc",
                "page_size": PAGE_SIZE, "page": page,
            }))
            if str(payload.get("generation_id")) != generation:
                raise SourceError("목록 조회 중 generation이 바뀌었습니다")
            count = payload.get("total")
            if type(count) is not int or count < 0 or (total is not None and total != count):
                raise SourceError("이벤트 총수가 잘못됐거나 변경됐습니다")
            total = count
            page_count = max(1, math.ceil(total / PAGE_SIZE))
            if page_count > MAX_PAGES:
                raise SourceError(f"전체 목록이 조회 예산 {MAX_PAGES}페이지를 넘습니다")
            rows = payload.get("events")
            expected = min(PAGE_SIZE, max(0, total - (page - 1) * PAGE_SIZE))
            if not isinstance(rows, list) or len(rows) != expected:
                raise SourceError("목록 페이지가 누락됐습니다")
            for event in rows:
                if not isinstance(event, dict):
                    raise SourceError("잘못된 이벤트 행입니다")
                identity = str(event.get("id") or "")
                if (not identity or identity in seen or event.get("data_status") != "ok"
                        or str(event.get("generation_id")) != generation):
                    raise SourceError("이벤트가 중복됐거나 generation·상태가 다릅니다")
                seen.add(identity)
                events.append(event)
            if page == page_count:
                break
        self.requests["trending"] += 1
        try:
            trending = self._get("api/polymarket/trending")
        except SourceError:
            trending = {}
        if str(trending.get("generation_id")) != generation or trending.get("state") != "ok":
            trending = {}
        return Snapshot(summary, tuple(events), trending)

    def detail(self, event_id: str, generation_id: str) -> dict[str, Any]:
        if self.requests["details"] >= MAX_DETAILS:
            raise SourceError("상세 조회 예산을 넘었습니다")
        self.requests["details"] += 1
        result = self._get("api/polymarket/events/" + quote(event_id, safe=""))
        if str(result.get("generation_id")) != generation_id or str(result.get("id")) != event_id:
            raise SourceError("상세 조회 중 generation 또는 이벤트 ID가 바뀌었습니다")
        return result

    def confirm(self, generation_id: str) -> None:
        if str(self._summary()["generation_id"]) != generation_id:
            raise SourceError("원고 생성 전에 generation이 바뀌었습니다")

    def news(self, title: str, *, reference: str) -> list[dict[str, str]]:
        """RSS 제목만 확인한다. 본문을 읽었다고 주장하지 않는다."""
        if self.requests["news"] >= MAX_DETAILS:
            raise SourceError("뉴스 조회 예산을 넘었습니다")
        self.requests["news"] += 1
        query = re.sub(r"[?\"<>]", " ", title).strip()[:180] + " when:7d"
        url = "https://news.google.com/rss/search?" + urlencode({
            "q": query, "hl": "en-US", "gl": "US", "ceid": "US:en",
        })
        try:
            response = self.session.get(url, timeout=min(self.timeout, 10))
            response.raise_for_status()
            if len(response.content) > 512_000:
                raise ValueError("RSS가 너무 큽니다")
            root = ElementTree.fromstring(response.content)
            as_of = datetime.fromisoformat(reference)
            result, seen = [], set()
            for item in root.findall("./channel/item"):
                headline = html.unescape(item.findtext("title", "")).strip()
                link = item.findtext("link", "").strip()
                try:
                    published = parsedate_to_datetime(item.findtext("pubDate", ""))
                    if published.tzinfo is None or not as_of - timedelta(days=7) <= published <= as_of:
                        continue
                except (TypeError, ValueError, OverflowError):
                    continue
                if (not headline or len(headline) > 300 or headline.casefold() in seen
                        or urlparse(link).scheme != "https"):
                    continue
                seen.add(headline.casefold())
                result.append({"id": f"news:{len(result) + 1}", "title": headline,
                               "url": link, "publisher": item.findtext("source", ""),
                               "published_at": published.isoformat()})
                if len(result) == MAX_NEWS_ITEMS:
                    break
            return result
        except (requests.RequestException, ElementTree.ParseError, ValueError) as exc:
            logger.warning("관련 뉴스 검색 미완료: %s", type(exc).__name__)
            return []
