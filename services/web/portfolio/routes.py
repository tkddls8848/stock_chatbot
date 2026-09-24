"""`/api/portfolio/*` — 개인 화면의 REST 라우트.

**주소는 명사(자원)이고 동작은 HTTP 메서드가 정한다**(`code_guide.md`).

| 자원 | 메서드 |
|---|---|
| `session` | `GET` 잠금 상태 · `POST` 잠금 해제 · `DELETE` 잠금 |
| `assets` | `GET` 목록 · `POST` 추가 / `assets/{id}`: `PUT` 수정 · `DELETE` 삭제 |
| `watchlist` | `GET` 조회 · `PUT` 전체 교체(봇과 같이 쓰는 파일, 잠금) |
| `advice` | `GET` 최근 목록 · `POST` 새 조언(201) / `advice/latest`·`advice/{id}`: `GET` |

`session`의 `GET`·`POST`를 뺀 전부가 잠금 뒤에 있다. 비밀번호가 설정되지 않았으면
전부 503이다. 쓰기는 JSON 본문만 받는다 — 교차 사이트 폼 전송이 끼어들 수 없고,
쿠키는 SameSite=Strict다.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from services.web.core.storage import FileLockTimeout
from services.web.portfolio.auth import LoginThrottle, password_matches, session_token, session_valid
from services.web.portfolio.service import AdviceBusy, AdviceLimit, AdviceService
from services.web.portfolio.store import AdviceStore, AssetStore, StoreError, WatchlistStore, canonical_code

_CANONICAL = re.compile(r"\d{6}|\d{5}|KR:(KOSPI|KOSDAQ):\d{6}|US:(NASDAQ|NYSE):[A-Z][A-Z0-9.-]{0,14}")
_KIND_FIELDS = {
    "stock": ("market", "code"),
    "bond": ("bond_type", "rate_pct", "maturity"),
    "deposit": ("product", "rate_pct", "maturity"),
    "real_estate": ("region_code", "complex", "area_m2", "loan_krw"),
}
_MAX_KRW = 10**13


class AssetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["stock", "bond", "deposit", "real_estate"]
    name: str = Field(min_length=1, max_length=60)
    value_krw: int = Field(ge=0, le=_MAX_KRW, description="현재 평가액(원)")
    cost_krw: int | None = Field(None, ge=0, le=_MAX_KRW, description="매입가·원금(원)")
    note: str = Field("", max_length=200)
    market: Literal["", "KR", "US", "CN", "HK", "JP"] = ""
    code: str = Field("", max_length=20)
    rate_pct: float | None = Field(None, ge=0, le=100)
    maturity: date | None = None
    product: Literal["", "deposit", "saving"] = ""
    bond_type: Literal["", "government", "corporate", "other"] = ""
    region_code: str = Field("", pattern=r"^(\d{5})?$", description="법정동코드 앞 5자리(시군구)")
    complex: str = Field("", max_length=40)
    area_m2: float | None = Field(None, gt=0, le=10_000)
    loan_krw: int | None = Field(None, ge=0, le=_MAX_KRW)

    def row(self) -> dict[str, Any]:
        """그 자산군에 맞는 칸만 저장한다. 다른 자산군의 칸은 버린다."""
        data = self.model_dump(mode="json")
        keep = {"kind", "name", "value_krw", "cost_krw", "note", *_KIND_FIELDS[self.kind]}
        return {key: value for key, value in data.items() if key in keep and value not in (None, "")}


class WatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    market: Literal["", "CN", "HK", "KR", "US"] = ""
    exchange: str = Field("", max_length=10)


class WatchlistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WatchItem] = Field(max_length=500)


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=200)


def build_router(
    *,
    password: str,
    cookie_name: str,
    max_age: int,
    throttle: LoginThrottle,
    assets: AssetStore,
    watchlist: WatchlistStore,
    advice_store: AdviceStore,
    advice: AdviceService,
    max_assets: int,
    max_watchlist: int,
) -> APIRouter:
    router = APIRouter(prefix="/api/portfolio")

    def configured() -> None:
        if not password:
            raise HTTPException(status_code=503, detail="개인 화면이 설정되지 않았습니다(PORTFOLIO_PASSWORD).")

    def unlocked(request: Request) -> None:
        configured()
        if not session_valid(request.cookies.get(cookie_name), password):
            raise HTTPException(status_code=401, detail="잠겨 있습니다. 비밀번호로 여세요.")

    def store_call(func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except StoreError as error:
            raise HTTPException(status_code=500, detail=f"저장 파일을 읽을 수 없습니다: {error}") from error
        except FileLockTimeout as error:
            raise HTTPException(status_code=503, detail="다른 작업이 파일을 쓰는 중입니다. 잠시 뒤 다시 시도하세요.") from error

    # ── session ──
    @router.get("/session")
    def session_state(request: Request) -> dict[str, bool]:
        return {"configured": bool(password),
                "unlocked": session_valid(request.cookies.get(cookie_name), password)}

    @router.post("/session", status_code=204, dependencies=[Depends(configured)])
    def open_session(body: SessionIn, request: Request, response: Response) -> None:
        client = request.client.host if request.client else "?"
        if throttle.blocked(client):
            raise HTTPException(status_code=429, detail="여러 번 틀렸습니다. 잠시 뒤 다시 시도하세요.")
        if not password_matches(body.password, password):
            throttle.fail(client)
            raise HTTPException(status_code=401, detail="비밀번호가 맞지 않습니다.")
        throttle.reset(client)
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(cookie_name, session_token(password), max_age=max_age, httponly=True,
                            secure=secure, samesite="strict", path="/")

    @router.delete("/session", status_code=204)
    def close_session(response: Response) -> None:
        response.delete_cookie(cookie_name, path="/")

    # ── assets ──
    @router.get("/assets", dependencies=[Depends(unlocked)])
    def list_assets() -> dict[str, Any]:
        return {"assets": store_call(assets.list)}

    @router.post("/assets", status_code=201, dependencies=[Depends(unlocked)])
    def add_asset(body: AssetIn) -> dict[str, Any]:
        try:
            return store_call(assets.add, body.row(), limit=max_assets)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.put("/assets/{asset_id}", dependencies=[Depends(unlocked)])
    def replace_asset(asset_id: str, body: AssetIn) -> dict[str, Any]:
        row = store_call(assets.replace, asset_id, body.row())
        if row is None:
            raise HTTPException(status_code=404, detail="그 자산이 없습니다.")
        return row

    @router.delete("/assets/{asset_id}", status_code=204, dependencies=[Depends(unlocked)])
    def delete_asset(asset_id: str) -> None:
        if not store_call(assets.delete, asset_id):
            raise HTTPException(status_code=404, detail="그 자산이 없습니다.")

    # ── watchlist ──
    @router.get("/watchlist", dependencies=[Depends(unlocked)])
    def get_watchlist() -> dict[str, Any]:
        return {"items": store_call(watchlist.get)}

    @router.put("/watchlist", dependencies=[Depends(unlocked)])
    def put_watchlist(body: WatchlistIn) -> dict[str, Any]:
        items: dict[str, str] = {}
        for item in body.items:
            code = (canonical_code(item.market, item.exchange, item.code) if item.market
                    else item.code.strip().upper())
            if code is None or not _CANONICAL.fullmatch(code):
                raise HTTPException(status_code=422, detail=f"종목 코드를 알 수 없습니다: {item.code}")
            items[code] = item.name.strip()
        if len(items) > max_watchlist:
            raise HTTPException(status_code=422, detail=f"관심종목은 {max_watchlist}개까지입니다.")
        return {"items": store_call(watchlist.replace, items)}

    # ── advice ──
    @router.get("/advice", dependencies=[Depends(unlocked)])
    def list_advice() -> dict[str, Any]:
        rows = []
        for advice_id in store_call(advice_store.ids):
            item = store_call(advice_store.get, advice_id) or {}
            rows.append({"id": advice_id, "created_at": item.get("created_at"),
                         "llm_status": item.get("llm_status")})
        return {"usage": store_call(advice.usage), "items": rows}

    @router.post("/advice", status_code=201, dependencies=[Depends(unlocked)])
    def create_advice() -> dict[str, Any]:
        try:
            return store_call(advice.create)
        except AdviceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AdviceLimit as error:
            raise HTTPException(status_code=429, detail=str(error)) from error

    @router.get("/advice/latest", dependencies=[Depends(unlocked)])
    def latest_advice() -> dict[str, Any]:
        item = store_call(advice_store.latest)
        if item is None:
            raise HTTPException(status_code=404, detail="아직 조언이 없습니다.")
        return item

    @router.get("/advice/{advice_id}", dependencies=[Depends(unlocked)])
    def get_advice(advice_id: str) -> dict[str, Any]:
        item = store_call(advice_store.get, advice_id)
        if item is None:
            raise HTTPException(status_code=404, detail="그 조언이 없습니다.")
        return item

    return router
