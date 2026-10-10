"""공개 시장정보와 Google 계정별 개인 리서치·자산관리·뉴스레터 구독 서버."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.web.pages import (
    ABOUT_HTML,
    INDEX_HTML,
    POLYMARKET_HTML,
    RESEARCH_HTML,
    ROBOTS_TXT,
    TERMS_HTML,
)
from services.web.pages.portfolio import PORTFOLIO_HTML
from services.web.pages.newsletter import UNSUBSCRIBE_CONFIRM_HTML
from services.web.pages.privacy import PRIVACY_HTML
from services.web.pages.search import SEARCH_HTML
from services.web.pages.errors import ERROR_HTML
from services.web.core.config import PUBLIC_DIR
from services.web.core import config
from services.web.accounts import Accounts
from services.web.newsletter.routes import build_newsletter_router, build_unsubscribe_router
from services.web.polymarket.repository import PolymarketRepository, make_etag
from services.web.portfolio.routes import build_router
from services.web.search import NewsSearch

POLYMARKET_REPOSITORY = PolymarketRepository(PUBLIC_DIR / "polymarket")
# 화면 글꼴은 저장소에 넣은 서브셋 파일을 이 프로세스가 직접 내려준다(외부 CDN을 부르지 않는다).
# 이름을 고정 목록으로만 받아 경로 조작이 끼어들 틈을 두지 않는다. 파일 이름에 내용이 바뀌면
# 이름도 바꾸는 규칙이라 1년 캐시(immutable)로 둔다.
FONT_DIR = Path(__file__).parent / "static" / "fonts"
FONTS = {"pretendard-sub.woff2", "noto-serif-kr-bold-sub.woff2"}
logger = logging.getLogger(__name__)


# 첫 화면 "오늘의 영상"의 YouTube 플레이어만 들인다. 다른 화면은 프레임을 하나도 허용하지 않는다.
_YOUTUBE_FRAME = "https://www.youtube-nocookie.com"


def _content_security_policy(html: str = "", *, frame_src: str = "'none'") -> str:
    """정적 화면과 함께 기동 시 한 번만 계산한다. 공백도 해시 입력의 일부다."""
    def hashes(tag: str) -> str:
        blocks = re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, re.DOTALL | re.IGNORECASE)
        return " ".join(sorted({
            "'sha256-" + base64.b64encode(hashlib.sha256(block.encode()).digest()).decode() + "'"
            for block in blocks
        })) or "'none'"

    # 차트는 같은 출처, 배경 질감은 data: SVG다. API 요청은 같은 출처만 허용한다.
    return (
        "default-src 'none'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors 'none'; frame-src " + frame_src + "; form-action 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "script-src " + hashes("script") + "; script-src-attr 'none'; "
        "style-src " + hashes("style") + "; style-src-attr 'none'"
    )


_PAGE_CSP = {path: _content_security_policy(html) for path, html in {
    "/forecast": POLYMARKET_HTML, "/research": RESEARCH_HTML,
    "/about": ABOUT_HTML, "/terms": TERMS_HTML, "/search": SEARCH_HTML,
    "/portfolio": PORTFOLIO_HTML, "/privacy": PRIVACY_HTML,
    # 확인·완료·오류 세 화면이 같은 셸이라 스크립트·스타일 해시가 같다.
    "/newsletter/unsubscribe": UNSUBSCRIBE_CONFIRM_HTML,
}.items()} | {"/": _content_security_policy(INDEX_HTML, frame_src=_YOUTUBE_FRAME)}
_ERROR_CSP = {status: _content_security_policy(html) for status, html in ERROR_HTML.items()}
_DEFAULT_CSP = _content_security_policy()
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}
# 브라우저의 기본 favicon 요청에 파일 없이 응답한다.
_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="8" fill="#f5f2ea"/>'
    '<path d="M6 23l7-8 5 4 8-12" fill="none" stroke="#75551b" stroke-width="3"/>'
    '</svg>'
)


def _error_response(request: Request, status: int) -> Response:
    if request.url.path.startswith("/api/"):
        detail = "Not Found" if status == 404 else "Internal Server Error"
        return JSONResponse({"detail": detail}, status_code=status)
    return HTMLResponse(ERROR_HTML[status], status_code=status,
                        headers={"Content-Security-Policy": _ERROR_CSP[status]})


def _read_json(name: str) -> dict[str, Any]:
    try:
        value = json.loads((PUBLIC_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def build_accounts() -> Accounts:
    return Accounts(config.USERS_DIR, client_id=config.GOOGLE_CLIENT_ID,
                    client_secret=config.GOOGLE_CLIENT_SECRET, identity_key=config.ACCOUNT_IDENTITY_KEY,
                    origin=config.AUTH_ORIGIN,
                    ready=bool(config.PRIVACY_OPERATOR and config.PRIVACY_CONTACT))


def build_app(portfolio_router: APIRouter | None = None, *, accounts: Accounts | None = None,
              research_router: APIRouter | None = None) -> FastAPI:
    app = FastAPI(title="Stock Chatbot", docs_url=None, redoc_url=None, openapi_url=None)
    search_repository = NewsSearch(PUBLIC_DIR)
    accounts = accounts or build_accounts()
    app.include_router(accounts.router())
    app.include_router(portfolio_router or build_router(accounts=accounts))
    app.include_router(build_newsletter_router(accounts))
    app.include_router(build_unsubscribe_router(accounts))
    from services.web.personal_research import build_research_router
    app.include_router(research_router or build_research_router(accounts))

    @app.middleware("http")
    async def response_policy(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            # 예외 메시지에는 비밀값이 섞일 수 있어 응답·로그 모두에 옮기지 않는다.
            # 경로와 예외 종류만 남긴다 — 그것도 없으면 어느 화면이 왜 깨졌는지 모른다.
            logger.error("웹 요청 처리 실패 path=%s error=%s", request.url.path, type(exc).__name__)
            response = _error_response(request, 500)
        response.headers.update(_SECURITY_HEADERS)
        response.headers.setdefault(
            "Content-Security-Policy", _PAGE_CSP.get(request.url.path, _DEFAULT_CSP)
        )
        # 개인 화면은 어떤 캐시(브라우저·프록시)에도 남기지 않는다.
        if request.url.path.startswith(("/portfolio", "/research", "/auth/", "/api/portfolio", "/api/research", "/api/account",
                                        "/newsletter/")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Vary"] = "Cookie"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        if request.method == "HEAD":
            # Content-Length와 Set-Cookie를 포함한 GET 헤더는 그대로 보존한다.
            head = Response(status_code=response.status_code)
            head.raw_headers = response.raw_headers
            return head
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        # API의 의도된 HTTP 오류(개인 저장소 손상 안내 포함)는 계약을 보존한다.
        if exc.status_code in (404, 500) and not request.url.path.startswith("/api/"):
            return _error_response(request, exc.status_code)
        return await http_exception_handler(request, exc)

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    def favicon() -> Response:
        return Response(_FAVICON, media_type="image/svg+xml")

    @app.api_route("/portfolio", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def portfolio_page() -> str:
        # 화면은 정적 껍데기다. 로그인 후 개인 API에서 값을 읽는다.
        return PORTFOLIO_HTML

    @app.api_route("/privacy", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def privacy_page() -> str:
        return PRIVACY_HTML

    @app.api_route("/search", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def search_page() -> str:
        return SEARCH_HTML

    @app.api_route("/api/search", methods=["GET", "HEAD"])
    def search(
        q: str = Query(default="", max_length=200),
        market: Literal["", "CN", "HK", "US", "KR", "JP", "EU"] = "",
        days: int | None = Query(default=None, ge=1, le=30),
        page: int = Query(default=1, ge=1, le=1000),
    ) -> Response:
        try:
            payload = search_repository.search(q, market=market, days=days, page=page)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.api_route("/research", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def research_page() -> str:
        return RESEARCH_HTML

    @app.api_route("/about", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def about_page() -> str:
        return ABOUT_HTML

    @app.api_route("/terms", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def terms_page() -> str:
        # 상단 메뉴에는 없고 모든 화면의 꼬리말에서만 닿는다.
        return TERMS_HTML

    @app.api_route("/forecast", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def polymarket_page() -> str:
        return POLYMARKET_HTML

    @app.api_route("/robots.txt", methods=["GET", "HEAD"], response_class=PlainTextResponse)
    def robots() -> str:
        # 화면은 열어 두고 무거운 /api/ 면과 AI 수집 봇만 막는다. 근거와 목록은
        # services/web/pages/robots.py에 있고, 무시하는 봇을 실제로 끊는 것은 앞단
        # Caddy다(infra/Caddyfile.example의 @aibots).
        return ROBOTS_TXT

    @app.api_route("/api/market", methods=["GET", "HEAD"])
    def market() -> dict[str, Any]:
        return _read_json("market.json")

    @app.api_route("/api/meta", methods=["GET", "HEAD"])
    def meta() -> dict[str, Any]:
        return {key: value for key, value in _read_json("meta.json").items()
                if key != "research_generated_at"}

    @app.api_route("/api/shorts", methods=["GET", "HEAD"])
    def shorts() -> dict[str, Any]:
        # 쇼츠가 게시 뒤 쓰는 최신 영상(storage/public/shorts/ko.json). 영상 ID 형식이 아니면 내보내지 않는다 —
        # 화면이 이 값으로 플레이어 주소를 만든다.
        row = _read_json("shorts/ko.json")
        video = str(row.get("video_id") or "")
        return {"ko": {key: str(row.get(key) or "") for key in ("date", "video_id", "title")}
                if re.fullmatch(r"[\w-]{6,20}", video) else None}

    def polymarket_json(
        request: Request,
        payload: dict[str, Any],
        route: str,
        query: dict[str, Any] | None = None,
    ) -> Response:
        generation_id = str(payload.get("generation_id") or "none")
        etag = make_etag(generation_id, route, query or {})
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
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
        # 현재 형식이 아닌 단락은 "자료 없음"으로 다룬다(code_guide: 형식이 틀리면 자료 없음). 옛 단락에는 모델이
        # 다른 질문의 확률을 붙인 글이 있었다 — 배포 순간부터 파일이 새로 써지기 전까지도 내보내지 않는다.
        for group in payload.get("groups") or []:
            if isinstance(group, dict) and group.get("paragraph_format") != config.POLYMARKET_BRIEF_PARAGRAPH_FORMAT:
                group.pop("paragraph", None)
                group.pop("overview", None)
                if group.get("status") == "ok":
                    group["status"] = "failed"  # 화면이 "이번 주기에는 정리하지 못했습니다"를 보이게
        if not payload:
            raise HTTPException(status_code=503, detail="아직 섹터 브리프가 없습니다.")
        # ETag는 거른 뒤의 본문으로 만든다. generation만 쓰면 옛 단락을 담은 캐시가 304로 계속 살아남고,
        # 같은 generation 안에서 파일이 새로 써져도 바뀌지 않는다(7차 검수).
        body = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        return polymarket_json(request, payload, "sector_brief", {"body": body})

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
            "science_health", "weather_climate", "law_regulation", "composite", "other",
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

    @app.api_route("/fonts/{name}", methods=["GET", "HEAD"])
    def font_file(name: str) -> Response:
        if name not in FONTS:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(FONT_DIR / name, media_type="font/woff2",
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.api_route("/market_chart.png", methods=["GET", "HEAD"])
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
