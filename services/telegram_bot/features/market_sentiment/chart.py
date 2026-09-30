"""Rendering for the Telegram market-sentiment chart."""

from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO



MARKET_LABELS = {
    "CN": "China mainland",
    "HK": "Hong Kong",
    "US": "United States",
    "KR": "Korea",
    "JP": "Japan",
    "EU": "Europe",
    "OTHER": "Other",
}


# 웹 화면(`services/web/pages/shell.py`)과 같은 팔레트다. 막대는 한국 시장 관례를 따라
# 빨강이 긍정, 파랑이 부정이다 — 예전 초록·빨강은 화면의 다른 수치와 방향이 반대였다.
_INK, _MUT, _LINE = "#0d1b2a", "#4c5a6b", "#d3d9e1"
_POS, _NEG, _FLAT = "#b42331", "#1f57b0", "#8a96a5"
MARKET_COLORS = {
    "US": "#16324f", "KR": "#b08d57", "JP": "#2e7d6b", "CN": "#8e3b46", "HK": "#5b6fa8",
    "EU": "#6b4f8a",
}


def market_label(market: str) -> str:
    return MARKET_LABELS.get(market, market)


def _trend_series(points: list[dict]) -> tuple[list[datetime], list[float]]:
    """Convert categorical date strings to sorted date coordinates."""
    parsed = sorted(
        (
            datetime.fromisoformat(str(point["date"])),
            float(point["avg_sentiment"]),
        )
        for point in points
    )
    return [day for day, _ in parsed], [value for _, value in parsed]


def _trend_curve(
    dates: list[datetime], values: list[float], weights: list[float], *, bandwidth_days: float = 4.0,
) -> tuple[list[datetime], list[float]] | None:
    """가우시안 커널 국소 선형 회귀로 일별 감성의 추세선을 구한다.

    계산하는 날마다 모든 날을 거리에 따라 종 모양(가우시안)으로 가중해 직선을 맞춘다.
    가장 가까운 k개만 쓰는 LOWESS는 창이 움직일 때 점이 갑자기 들어오고 나가 선에 잔물결이
    생겼다(2026-09-28 운영자 지적). 가우시안은 가중이 끊기지 않아 곡선이 매끈하다.
    `bandwidth_days`(표준편차)가 곡선의 부드러움을 정한다. 기사가 많은 날일수록 표본 오차가
    작으므로 기사 수의 제곱근을 가중에 곱한다. 점이 4개 미만이면 추세라 부를 수 없어 None이다.
    """
    import numpy as np

    if len(dates) < 4:
        return None
    origin = dates[0]
    x = np.array([(day - origin).total_seconds() / 86400 for day in dates])
    y = np.array(values, dtype=float)
    w = np.sqrt(np.maximum(np.array(weights, dtype=float), 1.0))
    grid = np.linspace(x[0], x[-1], max(2, int((x[-1] - x[0]) * 4) + 1))
    fitted = []
    for point in grid:
        kernel = np.exp(-0.5 * ((x - point) / bandwidth_days) ** 2) * w
        design = np.column_stack([np.ones_like(x), x - point])
        gram = design.T @ (design * kernel[:, None])
        if abs(np.linalg.det(gram)) < 1e-9:
            fitted.append(float(np.average(y, weights=kernel)))
            continue
        intercept, _ = np.linalg.solve(gram, design.T @ (kernel * y))
        fitted.append(float(intercept))
    curve_dates = [origin + timedelta(days=float(offset)) for offset in grid]
    return curve_dates, [min(1.0, max(-1.0, value)) for value in fitted]


def render_market_chart(
    markets: dict[str, dict],
    lookback_days: int,
) -> BytesIO:
    """Return a PNG with latest market mood ranking and daily sentiment trends.

    Always the same two-panel chart.
    """
    # Telegram handlers run outside the process main thread.  A GUI backend
    # attempts to create a window there, so force Matplotlib's file-only backend
    # before importing pyplot.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    ordered = sorted(markets.items(), key=lambda item: item[1]["avg_sentiment"], reverse=True)
    labels = [market_label(key) for key, _ in ordered]
    values = [item["avg_sentiment"] for _, item in ordered]
    colors = [_POS if value > 0.1 else _NEG if value < -0.1 else _FLAT for value in values]

    fig, (ranking_ax, trend_ax) = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"width_ratios": [0.9, 1.4]})
    fig.patch.set_facecolor("#ffffff")
    for ax in (ranking_ax, trend_ax):
        ax.set_facecolor("#ffffff")
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color(_LINE)
        ax.tick_params(colors=_MUT, labelsize=9)
        ax.xaxis.label.set_color(_MUT)
        ax.yaxis.label.set_color(_MUT)
    ranking_ax.barh(labels, values, color=colors, height=0.5)
    ranking_ax.axvline(0, color=_MUT, linewidth=0.9)
    ranking_ax.tick_params(axis="y", length=0, labelcolor=_INK)
    ranking_ax.grid(axis="x", color=_LINE, linewidth=0.6)
    ranking_ax.set_axisbelow(True)
    ranking_ax.set_xlim(-1, 1)
    ranking_ax.set_title(f"Average news sentiment ({lookback_days}d)", loc="left", fontsize=11,
                         fontweight="bold", color=_INK, pad=12)
    ranking_ax.set_xlabel("-1 negative     0 neutral     +1 positive")
    ranking_ax.invert_yaxis()
    for index, value in enumerate(values):
        ranking_ax.text(value + (0.03 if value >= 0 else -0.03), index, f"{value:+.2f}", va="center", ha="left" if value >= 0 else "right", fontsize=9,
                        fontweight="bold", color=_POS if value > 0.1 else _NEG if value < -0.1 else _MUT)

    # 날마다 찍은 점은 흐리게 두고, 그 위에 비선형 추세선을 굵게 그린다(2026-09-28 운영자 요청).
    # 30일 치 점을 선으로 이으면 하루하루의 잡음만 보이고 방향은 읽히지 않는다.
    for market, stats in ordered:
        dates, sentiments = _trend_series(stats["daily"])
        counts = [point.get("count", 0) for point in sorted(stats["daily"], key=lambda point: str(point["date"]))]
        curve = _trend_curve(dates, sentiments, counts)
        (line,) = trend_ax.plot(
            *(curve or (dates, sentiments)),
            linewidth=2.2,
            color=MARKET_COLORS.get(market),
            label=market_label(market),
        )
        trend_ax.scatter(dates, sentiments, s=14, alpha=0.35, color=line.get_color(), linewidths=0)
    trend_ax.axhline(0, color=_MUT, linewidth=0.9)
    trend_ax.set_ylim(-1, 1)
    trend_ax.set_title(f"Sentiment trend ({lookback_days}d, kernel regression)", loc="left", fontsize=11,
                       fontweight="bold", color=_INK, pad=12)
    trend_ax.set_ylabel("Average sentiment")
    trend_ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    trend_ax.tick_params(axis="x", rotation=45)
    trend_ax.legend(loc="upper left", frameon=False, ncol=6, fontsize=8.5, bbox_to_anchor=(0, 1.0),
                    handlelength=1.6, columnspacing=1.2)
    trend_ax.grid(axis="y", color=_LINE, linewidth=0.6)
    trend_ax.set_axisbelow(True)
    fig.tight_layout()

    image = BytesIO()
    image.name = "market_sentiment.png"
    fig.savefig(image, format="png", dpi=160, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    image.seek(0)
    return image
