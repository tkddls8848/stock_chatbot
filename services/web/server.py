"""읽기 전용 공개 웹.

별도 프로세스로 실행하며 ``storage/public`` 산출물만 읽는다. 인증과 TLS는 이
프로세스 앞단의 Caddy가 담당한다.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

from services.web.pages import ABOUT_HTML, INDEX_HTML, POLYMARKET_HTML, RESEARCH_HTML, ROBOTS_TXT
from services.web.pages.search import SEARCH_HTML
from services.web.core.config import PUBLIC_DIR
from services.web.polymarket.repository import PolymarketRepository, make_etag
from services.web.search import NewsSearch

POLYMARKET_REPOSITORY = PolymarketRepository(PUBLIC_DIR / "polymarket")


def _read_json(name: str) -> dict[str, Any]:
    try:
        value = json.loads((PUBLIC_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def build_app() -> FastAPI:
    app = FastAPI(title="Stock Chatbot", docs_url=None, redoc_url=None, openapi_url=None)
    search_repository = NewsSearch(PUBLIC_DIR)

    @app.get("/search", response_class=HTMLResponse)
    def search_page() -> str:
        return SEARCH_HTML

    @app.get("/api/search")
    def search(
        q: str = Query(default="", max_length=200),
        market: Literal["", "CN", "HK", "US", "KR", "JP"] = "",
        days: int | None = Query(default=None, ge=1, le=30),
        page: int = Query(default=1, ge=1, le=1000),
    ) -> Response:
        try:
            payload = search_repository.search(q, market=market, days=days, page=page)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.get("/research", response_class=HTMLResponse)
    def research_page() -> str:
        return RESEARCH_HTML

    @app.get("/about", response_class=HTMLResponse)
    def about_page() -> str:
        return ABOUT_HTML

    @app.api_route("/forecast", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def polymarket_page() -> str:
        return POLYMARKET_HTML

    @app.api_route("/robots.txt", methods=["GET", "HEAD"], response_class=PlainTextResponse)
    def robots() -> str:
        # 화면은 열어 두고 무거운 /api/ 면과 AI 수집 봇만 막는다. 근거와 목록은
        # services/web/pages/robots.py에 있고, 무시하는 봇을 실제로 끊는 것은 앞단
        # Caddy다(infra/Caddyfile.example의 @aibots).
        return ROBOTS_TXT

    @app.get("/api/market")
    def market() -> dict[str, Any]:
        return _read_json("market.json")

    @app.get("/api/research")
    def research() -> dict[str, Any]:
        return _read_json("research.json")

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return _read_json("meta.json")

    def polymarket_json(
        request: Request,
        payload: dict[str, Any],
        route: str,
        query: dict[str, Any] | None = None,
    ) -> Response:
        generation_id = str(payload.get("generation_id") or "none")
        etag = make_etag(generation_id, route, query or {})
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "no-cache"})

    def require_manifest() -> None:
        if not POLYMARKET_REPOSITORY.load():
            raise HTTPException(status_code=503, detail="예측 컨센서스 현재 수집분이 없습니다.")

    @app.api_route("/api/forecast/summary", methods=["GET", "HEAD"])
    def polymarket_summary(request: Request, include_flagged: bool = False) -> Response:
        require_manifest()
        return polymarket_json(
            request,
            POLYMARKET_REPOSITORY.summary(include_flagged=include_flagged),
            "summary",
            {"include_flagged": include_flagged},
        )

    @app.api_route("/api/forecast/categories", methods=["GET", "HEAD"])
    def polymarket_categories(request: Request) -> Response:
        require_manifest()
        return polymarket_json(
            request, POLYMARKET_REPOSITORY.categories(), "categories"
        )

    @app.api_route("/api/forecast/sector-brief", methods=["GET", "HEAD"])
    def polymarket_sector_brief(request: Request) -> Response:
        payload = _read_json("polymarket/sector_brief.json")
        # previous는 다음 실행이 이동을 계산할 기준일 뿐이다. 화면이 쓰지 않고
        # event 수천 건짜리라 내보내지 않는다.
        payload.pop("previous", None)
        if not payload:
            raise HTTPException(status_code=503, detail="아직 섹터 브리프가 없습니다.")
        return polymarket_json(request, payload, "sector_brief")

    @app.api_route("/api/forecast/trending", methods=["GET", "HEAD"])
    def polymarket_trending(request: Request) -> Response:
        payload = _read_json("polymarket/trending.json")
        # baseline·previous는 다음 주기가 이동을 계산할 상태일 뿐이다. 화면이
        # 읽지 않고 후보 수백 건짜리라 내보내지 않는다.
        payload.pop("baseline", None)
        payload.pop("previous", None)
        if not payload:
            raise HTTPException(status_code=503, detail="아직 트렌드 집계가 없습니다.")
        return polymarket_json(request, payload, "trending")

    @app.api_route("/api/forecast/health", methods=["GET", "HEAD"])
    def polymarket_health(request: Request) -> Response:
        payload = POLYMARKET_REPOSITORY.health()
        return polymarket_json(request, payload, "health")

    @app.api_route("/api/forecast/events", methods=["GET", "HEAD"])
    def polymarket_events(
        request: Request,
        category: Literal[
            "politics", "geopolitics", "economy_finance", "crypto",
            "technology_ai", "business", "sports", "culture",
            "science_health", "weather_climate", "law_regulation", "other",
        ] | None = None,
        tag: str | None = None,
        region: str | None = None,
        event_type: Literal[
            "binary", "exclusive_multi", "independent_multi", "unknown_multi"
        ] | None = None,
        status: Literal[
            "ok", "low_liquidity", "no_liquidity", "liquidity_missing", "unavailable"
        ] | None = None,
        q: str | None = Query(default=None, max_length=200),
        sort: Literal[
            "relevance", "volume24hr", "liquidity", "leader_probability", "end_date", "title"
        ] | None = None,
        order: Literal["asc", "desc"] = "desc",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=25, ge=1, le=100),
    ) -> Response:
        require_manifest()
        known_query = {
            "category": category,
            "tag": tag,
            "region": region,
            "event_type": event_type,
            "status": status,
            "q": q,
            "sort": sort,
            "order": order,
            "page": page,
            "page_size": page_size,
            # 주석만 바뀐 주기에도 결과가 달라진다. generation만 보면 304로 옛
            # 결과를 돌려준다.
            "index": POLYMARKET_REPOSITORY.index_version(),
        }
        payload = POLYMARKET_REPOSITORY.events(
            category=category,
            tag=tag,
            region=region,
            event_type=event_type,
            status=status,
            query=q,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
        return polymarket_json(request, payload, "events", known_query)

    @app.api_route("/api/forecast/events/{event_id}", methods=["GET", "HEAD"])
    def polymarket_event_detail(event_id: str, request: Request) -> Response:
        require_manifest()
        payload = POLYMARKET_REPOSITORY.detail(event_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="이 event가 현재 generation에 없습니다.")
        return polymarket_json(request, payload, "event_detail", {"event_id": event_id})

    @app.get("/market_chart.png")
    def market_chart(request: Request) -> Response:
        path = PUBLIC_DIR / "market_chart.png"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="시장 산출물이 아직 없습니다.")
        # URL은 고정이고 파일만 새로 구워진다. 캐시 지시가 없으면 브라우저가
        # Last-Modified로 휴리스틱 유효기간을 잡아(RFC 9111 4.2.2) 새 차트를
        # 며칠씩 건너뛴다 — 화면이 옛 추이를 계속 보여 주는 원인이다.
        # 매번 되묻게 하고, 안 바뀌었으면 304로 끝낸다.
        stat = path.stat()
        etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type="image/png", headers=headers)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        build_app(),
        host=os.environ.get("WEBPUB_HOST", "127.0.0.1"),
        port=int(os.environ.get("WEBPUB_PORT", "8788")),
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
