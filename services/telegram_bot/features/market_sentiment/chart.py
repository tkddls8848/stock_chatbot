"""Rendering for the Telegram market-sentiment chart."""

from __future__ import annotations

from datetime import datetime
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


# σ가 이보다 작으면 하루 차이가 과장된다(기사 몇 건만 있던 시장).
_MIN_SPREAD = 0.05


def _common_baseline(markets: dict[str, dict]) -> float:
    """전 시장·전 기간의 기사 수 가중 평균 논조. 감성 모델의 공통 쏠림(대체로 약간 긍정)을 뺄 기준이다."""
    total = weight = 0.0
    for stats in markets.values():
        for point in stats.get("daily") or []:
            count = max(float(point.get("count") or 0), 1.0)
            total += float(point["avg_sentiment"]) * count
            weight += count
    return total / weight if weight else 0.0


def _cumulative_tone(
    dates: list[datetime], values: list[float], baseline: float,
) -> tuple[list[datetime], list[float]] | None:
    """누적 논조선: 날마다 (논조 − 공통 기준선)을 그 시장의 하루 변동폭(σ)으로 나눠 쌓는다.

    오르면 전 시장 평균보다 긍정적인 날이 이어지는 구간, 꺾이면 국면 전환, 기울기가 경향의 세기다.
    예전 가우시안 커널 회귀(표준편차 4일)는 앞뒤 약 16일을 섞어 선이 평균에 붙었다 — 30일 일별 값이
    ±0.3을 오가도 선은 폭 0.1~0.25로 평평했다(2026-10-04 운영자 지적). 누적은 평균으로 끌리지 않는다.
    기준선을 시장 자기 평균으로 두면 끝이 늘 0으로 돌아와 "지금"을 말하지 못하고, 0(중립)으로 두면
    모델의 긍정 쏠림이 쌓여 거의 모든 시장이 오르기만 해서, 전 시장 공통 평균을 쓴다.
    σ로 나누는 것은 변동이 큰 시장(미국)과 작은 시장(중국)을 같은 눈금에서 보려는 것이다.
    """
    import numpy as np

    if len(dates) < 2:
        return None
    y = np.array(values, dtype=float)
    spread = max(float(y.std()), _MIN_SPREAD)
    return dates, [float(v) for v in np.cumsum((y - baseline) / spread)]


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

    # 누적 논조선(2026-10-04 운영자 요청 — 시간에 따른 논조의 경향을 본다). 일별 점은 단위가 달라 찍지 않는다.
    baseline = _common_baseline(markets)
    for market, stats in ordered:
        dates, sentiments = _trend_series(stats["daily"])
        curve = _cumulative_tone(dates, sentiments, baseline)
        if curve is None:
            continue
        trend_ax.plot(*curve, linewidth=2.2, marker="o", markersize=2.5,
                      color=MARKET_COLORS.get(market), label=market_label(market))
    trend_ax.axhline(0, color=_MUT, linewidth=0.9)
    trend_ax.set_title(f"Cumulative tone vs all-market average ({lookback_days}d)", loc="left", fontsize=11,
                       fontweight="bold", color=_INK, pad=12)
    trend_ax.set_ylabel("Cumulative (daily tone − average) / σ")
    trend_ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    trend_ax.tick_params(axis="x", rotation=45)
    # 누적선은 위아래 끝까지 쓰므로 범례를 그림 밖 오른쪽에 둔다.
    trend_ax.legend(loc="upper left", frameon=False, fontsize=8.5, bbox_to_anchor=(1.01, 1.0), handlelength=1.6)
    trend_ax.grid(axis="y", color=_LINE, linewidth=0.6)
    trend_ax.set_axisbelow(True)
    fig.tight_layout()

    image = BytesIO()
    image.name = "market_sentiment.png"
    fig.savefig(image, format="png", dpi=160, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    image.seek(0)
    return image
