"""규칙 진단. **조언에 나오는 숫자는 전부 여기서 만든다.**

LLM은 이 결과를 해석만 한다(`advisor.py`). 모델이 쓴 금액·비율을 그대로 믿으면
검산할 수 없기 때문이다. 문턱은 `core/config.py`의 `PORTFOLIO_*` 상수다.
외부 자료가 없는 항목은 계산하지 않고 "자료 없음"으로 남긴다.

경보가 되는 발견에는 `key`와 `brief`를 단다. `key`는 같은 조건이 이어지는 동안 바뀌지 않는
이름이라 내 자산 점검(`watch.py`)이 "새로 넘은 기준"만 골라 편지로 알린다. `brief`는 금액을
빼고 비율·날짜만 남긴 한 줄이다 — 편지는 메일 서버를 거쳐 가므로 자산 금액을 싣지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from services.web.core.config import (
    PORTFOLIO_CLASS_CONCENTRATION,
    PORTFOLIO_LTV_WARNING,
    PORTFOLIO_MATURITY_WINDOW_DAYS,
    PORTFOLIO_MIN_LIQUID_SHARE,
    PORTFOLIO_RATE_GAP_PP,
    PORTFOLIO_SINGLE_CONCENTRATION,
)

CLASS_LABELS = {"stock": "주식", "bond": "채권", "deposit": "예적금", "real_estate": "부동산"}


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _won(value: float) -> str:
    """사람이 읽는 금액. 억·만 단위로 끊는다(예: 3억 2,500만원)."""
    value = round(value)
    eok, rest = divmod(abs(value), 100_000_000)
    man = rest // 10_000
    parts = []
    if eok:
        parts.append(f"{eok:,}억")
    if man or not eok:
        parts.append(f"{man:,}만")
    return ("-" if value < 0 else "") + " ".join(parts) + "원"


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def diagnose(
    assets: list[dict[str, Any]],
    *,
    today: date,
    deposit_rates: dict[str, Any],
    market_rates: dict[str, Any],
    estimates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []

    def note(level: str, code: str, message: str, asset_id: str | None = None, *,
             key: str = "", brief: str = "") -> None:
        findings.append({"level": level, "code": code, "message": message,
                         **({"asset_id": asset_id} if asset_id else {}),
                         **({"key": key, "brief": brief} if key else {})})

    total = float(sum(max(0, int(a.get("value_krw") or 0)) for a in assets))
    loans = float(sum(max(0, int(a.get("loan_krw") or 0)) for a in assets if a.get("kind") == "real_estate"))
    by_class: dict[str, dict[str, Any]] = {}
    for kind, label in CLASS_LABELS.items():
        value = float(sum(int(a.get("value_krw") or 0) for a in assets if a.get("kind") == kind))
        by_class[kind] = {"label": label, "value_krw": round(value), "share_pct": _pct(value, total)}

    if not assets or total <= 0:
        note("info", "empty", "입력된 자산이 없어 진단할 수 없다.")
        return {"as_of": today.isoformat(), "total_krw": 0, "findings": findings,
                "by_class": by_class, "data_status": {}}

    # ── 편중 ──
    for kind, row in by_class.items():
        if row["share_pct"] / 100 > PORTFOLIO_CLASS_CONCENTRATION:
            limit = round(PORTFOLIO_CLASS_CONCENTRATION * 100)
            note("warn", "class_concentration",
                 f"{row['label']} 비중이 {row['share_pct']}%로 한 자산군에 치우쳐 있다(기준 {limit}%).",
                 key=f"class:{kind}", brief=f"{row['label']} 비중 {row['share_pct']}%(기준 {limit}%)")
    for asset in assets:
        share = _pct(int(asset.get("value_krw") or 0), total)
        if share / 100 > PORTFOLIO_SINGLE_CONCENTRATION:
            limit = round(PORTFOLIO_SINGLE_CONCENTRATION * 100)
            note("warn", "single_concentration",
                 f"'{asset.get('name')}' 하나가 전체의 {share}%다(기준 {limit}%).", asset.get("id"),
                 key=f"single:{asset.get('id')}", brief=f"'{asset.get('name')}' 비중 {share}%(기준 {limit}%)")

    liquid_share = by_class["deposit"]["share_pct"]
    if liquid_share / 100 < PORTFOLIO_MIN_LIQUID_SHARE:
        limit = round(PORTFOLIO_MIN_LIQUID_SHARE * 100)
        note("warn", "low_liquidity",
             f"예적금 비중이 {liquid_share}%로 급할 때 꺼낼 돈이 얇다(기준 {limit}%).",
             key="liquidity", brief=f"예적금 비중 {liquid_share}%(기준 {limit}%)")

    # ── 만기 ──
    maturities = []
    for asset in assets:
        if asset.get("kind") not in ("deposit", "bond"):
            continue
        day = _parse_day(asset.get("maturity"))
        if day is None:
            continue
        left = (day - today).days
        if left <= PORTFOLIO_MATURITY_WINDOW_DAYS:
            maturities.append({"asset_id": asset.get("id"), "name": asset.get("name"),
                               "maturity": day.isoformat(), "days_left": left,
                               "value_krw": int(asset.get("value_krw") or 0)})
            state = "이미 지났다" if left < 0 else f"{left}일 남았다"
            # 만기가 다가오는 단계마다 한 번씩 다시 알린다(90·30·7일 전, 지난 뒤).
            stage = "past" if left < 0 else next(d for d in (7, 30, PORTFOLIO_MATURITY_WINDOW_DAYS) if left <= d)
            note("warn" if left <= 30 else "info", "maturity",
                 f"'{asset.get('name')}' 만기({day.isoformat()})가 {state}. "
                 f"{_won(int(asset.get('value_krw') or 0))}의 재예치·재투자 계획이 필요하다.",
                 asset.get("id"), key=f"maturity:{asset.get('id')}:{stage}",
                 brief=f"'{asset.get('name')}' 만기 {day.isoformat()}({'지남' if left < 0 else f'{left}일 남음'})")

    # ── 예적금 금리 차이 ──
    rate_gaps = []
    if deposit_rates.get("status") == "ok":
        for asset in assets:
            if asset.get("kind") != "deposit" or asset.get("rate_pct") is None:
                continue
            product = "saving" if asset.get("product") == "saving" else "deposit"
            best = (deposit_rates.get(product) or {}).get("best_rate_pct")
            if best is None:
                continue
            gap = round(best - float(asset["rate_pct"]), 2)
            rate_gaps.append({"asset_id": asset.get("id"), "name": asset.get("name"),
                              "rate_pct": float(asset["rate_pct"]), "best_rate_pct": best, "gap_pp": gap})
            if gap >= PORTFOLIO_RATE_GAP_PP:
                note("info", "rate_gap",
                     f"'{asset.get('name')}' 금리 {asset['rate_pct']}%는 은행권 12개월 최고 기본금리"
                     f" {best}%보다 {gap}%p 낮다. 만기·중도해지 조건과 함께 볼 일이다.",
                     asset.get("id"))

    # ── 채권 ──
    bonds = []
    treasury = (market_rates.get("treasury_3y") or {}).get("value_pct") if market_rates.get("status") == "ok" else None
    for asset in assets:
        if asset.get("kind") != "bond":
            continue
        coupon = asset.get("rate_pct")
        row = {"asset_id": asset.get("id"), "name": asset.get("name"), "coupon_pct": coupon,
               "treasury_3y_pct": treasury}
        if coupon is not None and treasury is not None:
            row["spread_pp"] = round(float(coupon) - treasury, 2)
        bonds.append(row)

    # ── 부동산 ──
    real_estate = []
    for asset in assets:
        if asset.get("kind") != "real_estate":
            continue
        value = int(asset.get("value_krw") or 0)
        loan = int(asset.get("loan_krw") or 0)
        row: dict[str, Any] = {"asset_id": asset.get("id"), "name": asset.get("name"),
                               "value_krw": value, "loan_krw": loan,
                               "ltv_pct": _pct(loan, value), "equity_krw": value - loan}
        estimate = estimates.get(str(asset.get("id")))
        if estimate and estimate.get("status") == "ok" and estimate.get("estimate_krw"):
            row.update({"estimate_krw": estimate["estimate_krw"], "basis": estimate.get("basis"),
                        "trade_count": estimate.get("trade_count"),
                        "estimate_gap_pct": _pct(estimate["estimate_krw"] - value, value)})
        real_estate.append(row)
        if value and loan / value > PORTFOLIO_LTV_WARNING:
            limit = round(PORTFOLIO_LTV_WARNING * 100)
            note("warn", "ltv",
                 f"'{asset.get('name')}' 대출 비율이 {row['ltv_pct']}%다(기준 {limit}%). 금리가 오르면 부담이 커진다.",
                 asset.get("id"), key=f"ltv:{asset.get('id')}",
                 brief=f"'{asset.get('name')}' 대출 비율 {row['ltv_pct']}%(기준 {limit}%)")
        if "estimate_gap_pct" in row and abs(row["estimate_gap_pct"]) >= 15:
            note("info", "estimate_gap",
                 f"'{asset.get('name')}' 입력 평가액과 최근 실거래 기반 추정치"
                 f"({_won(row['estimate_krw'])})가 {row['estimate_gap_pct']}% 다르다.",
                 asset.get("id"))

    return {
        "as_of": today.isoformat(),
        "total_krw": round(total),
        "total_text": _won(total),
        "loans_krw": round(loans),
        "net_worth_krw": round(total - loans),
        "net_worth_text": _won(total - loans),
        "by_class": by_class,
        "liquid_share_pct": liquid_share,
        "maturities": maturities,
        "rate_gaps": rate_gaps,
        "bonds": bonds,
        "real_estate": real_estate,
        "market_rates": {k: v for k, v in market_rates.items() if k != "status"},
        "deposit_best": {
            product: (deposit_rates.get(product) or {}).get("best_rate_pct")
            for product in ("deposit", "saving")
        } if deposit_rates.get("status") == "ok" else {},
        "findings": findings,
    }
