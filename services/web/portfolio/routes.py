"""Google 계정별 자산·관심종목·조언 REST API."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.web.core.storage import FileLockTimeout
from services.web.accounts import Account, Accounts
from services.web.core import config
from services.web.llm.factory import build_portfolio_advisor
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


class AdviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 개인 데이터를 외부 AI로 보내는 기능은 제공하지 않는다.
    use_ai: Literal[False] = False


def build_router(*, accounts: Accounts, advice_factory=None) -> APIRouter:
    router = APIRouter(prefix="/api/portfolio")

    def resources(account: Account = Depends(accounts.context)):
        assets = AssetStore(account.root / "assets.json")
        advice_store = AdviceStore(account.root / "advice")
        advice = (advice_factory(assets, advice_store) if advice_factory else AdviceService(
            assets=assets, advice=advice_store, public_dir=config.PUBLIC_DIR,
            max_daily=config.PORTFOLIO_ADVICE_MAX_DAILY,
            history_limit=config.PORTFOLIO_ADVICE_HISTORY_LIMIT,
            advisor_factory=build_portfolio_advisor,
        ))
        return assets, WatchlistStore(account.root / "watchlist.json"), advice_store, advice

    def store_call(func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except StoreError as error:
            raise HTTPException(status_code=500, detail="개인 저장 파일을 읽을 수 없습니다.") from error
        except FileLockTimeout as error:
            raise HTTPException(status_code=503, detail="다른 작업이 파일을 쓰는 중입니다. 잠시 뒤 다시 시도하세요.") from error

    # ── assets ──
    @router.get("/assets")
    def list_assets(stores=Depends(resources)) -> dict[str, Any]:
        assets, _, _, _ = stores
        return {"assets": store_call(assets.list)}

    @router.post("/assets", status_code=201)
    def add_asset(body: AssetIn, stores=Depends(resources)) -> dict[str, Any]:
        assets, _, _, _ = stores
        try:
            return store_call(assets.add, body.row(), limit=config.PORTFOLIO_MAX_ASSETS)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.put("/assets/{asset_id}")
    def replace_asset(asset_id: str, body: AssetIn, stores=Depends(resources)) -> dict[str, Any]:
        assets, _, _, _ = stores
        row = store_call(assets.replace, asset_id, body.row())
        if row is None:
            raise HTTPException(status_code=404, detail="그 자산이 없습니다.")
        return row

    @router.delete("/assets/{asset_id}", status_code=204)
    def delete_asset(asset_id: str, stores=Depends(resources)) -> None:
        assets, _, _, _ = stores
        if not store_call(assets.delete, asset_id):
            raise HTTPException(status_code=404, detail="그 자산이 없습니다.")

    # ── watchlist ──
    @router.get("/watchlist")
    def get_watchlist(stores=Depends(resources)) -> dict[str, Any]:
        _, watchlist, _, _ = stores
        return {"items": store_call(watchlist.get)}

    @router.put("/watchlist")
    def put_watchlist(body: WatchlistIn, stores=Depends(resources)) -> dict[str, Any]:
        _, watchlist, _, _ = stores
        items: dict[str, str] = {}
        for item in body.items:
            code = (canonical_code(item.market, item.exchange, item.code) if item.market
                    else item.code.strip().upper())
            if code is None or not _CANONICAL.fullmatch(code):
                raise HTTPException(status_code=422, detail=f"종목 코드를 알 수 없습니다: {item.code}")
            items[code] = item.name.strip()
        if len(items) > config.PORTFOLIO_MAX_WATCHLIST:
            raise HTTPException(status_code=422, detail=f"관심종목은 {config.PORTFOLIO_MAX_WATCHLIST}개까지입니다.")
        return {"items": store_call(watchlist.replace, items)}

    # ── advice ──
    @router.get("/advice")
    def list_advice(stores=Depends(resources)) -> dict[str, Any]:
        _, _, advice_store, advice = stores
        rows = []
        for advice_id in store_call(advice_store.ids):
            item = store_call(advice_store.get, advice_id) or {}
            rows.append({"id": advice_id, "created_at": item.get("created_at"),
                         "llm_status": item.get("llm_status")})
        return {"usage": store_call(advice.usage), "items": rows}

    @router.post("/advice", status_code=201)
    def create_advice(body: AdviceIn = AdviceIn(), stores=Depends(resources)) -> dict[str, Any]:
        _, _, _, advice = stores
        try:
            return store_call(advice.create, use_ai=body.use_ai)
        except AdviceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AdviceLimit as error:
            raise HTTPException(status_code=429, detail=str(error)) from error

    @router.get("/advice/latest")
    def latest_advice(stores=Depends(resources)) -> dict[str, Any]:
        _, _, advice_store, _ = stores
        item = store_call(advice_store.latest)
        if item is None:
            raise HTTPException(status_code=404, detail="아직 조언이 없습니다.")
        return item

    @router.get("/advice/{advice_id}")
    def get_advice(advice_id: str, stores=Depends(resources)) -> dict[str, Any]:
        _, _, advice_store, _ = stores
        item = store_call(advice_store.get, advice_id)
        if item is None:
            raise HTTPException(status_code=404, detail="그 조언이 없습니다.")
        return item

    return router
