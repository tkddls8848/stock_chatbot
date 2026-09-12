"""Rendering for the Telegram market-sentiment chart."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

import numpy as np


MARKET_LABELS = {
    "CN": "China mainland",
    "HK": "Hong Kong",
    "US": "United States",
    "KR": "Korea",
    "JP": "Japan",
    "EU": "Europe",
    "OTHER": "Other",
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
    colors = ["#16a34a" if value > 0.1 else "#dc2626" if value < -0.1 else "#64748b" for value in values]

    fig, (ranking_ax, trend_ax) = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"width_ratios": [0.9, 1.4]})
    fig.patch.set_facecolor("#f8fafc")
    ranking_ax.set_facecolor("#f8fafc")
    trend_ax.set_facecolor("#f8fafc")
    ranking_ax.barh(labels, values, color=colors, height=0.58)
    ranking_ax.axvline(0, color="#94a3b8", linewidth=0.9)
    ranking_ax.set_xlim(-1, 1)
    ranking_ax.set_title("Average news sentiment")
    ranking_ax.set_xlabel("-1 negative     0 neutral     +1 positive")
    ranking_ax.invert_yaxis()
    for index, value in enumerate(values):
        ranking_ax.text(value + (0.03 if value >= 0 else -0.03), index, f"{value:+.2f}", va="center", ha="left" if value >= 0 else "right", fontsize=9)

    for market, stats in ordered:
        dates, sentiments = _trend_series(stats["daily"])
        trend_ax.plot(
            dates,
            sentiments,
            marker="o",
            linewidth=2,
            label=market_label(market),
        )
    trend_ax.axhline(0, color="#94a3b8", linewidth=0.9)
    trend_ax.set_ylim(-1, 1)
    trend_ax.set_title(f"Daily sentiment trend ({lookback_days}d)")
    trend_ax.set_ylabel("Average sentiment")
    trend_ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    trend_ax.tick_params(axis="x", rotation=45)
    trend_ax.legend(loc="best", frameon=False)
    trend_ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()

    image = BytesIO()
    image.name = "market_sentiment.png"
    fig.savefig(image, format="png", dpi=160, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    image.seek(0)
    return image


def render_anomaly_chart(
    scored: dict[str, list],
    lookback_days: int,
    residual_markets: set[str],
) -> BytesIO:
    """Render ranking, return-vs-tone scatter, and rolling anomaly strength."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from scipy.stats import theilslopes

    fig = plt.figure(figsize=(12, 8))
    grid = fig.add_gridspec(2, 2, height_ratios=[1.15, 1.0])
    ranking_ax = fig.add_subplot(grid[0, 0])
    scatter_ax = fig.add_subplot(grid[0, 1])
    rolling_ax = fig.add_subplot(grid[1, :])
    fig.patch.set_facecolor("#f8fafc")
    for axis in (ranking_ax, scatter_ax, rolling_ax):
        axis.set_facecolor("#f8fafc")

    ranking = []
    for market, points in scored.items():
        recent = [
            point.anomaly_score
            for point in points[-lookback_days:]
            if market in residual_markets and point.anomaly_score is not None
        ]
        ranking.append((market, float(np.median(recent)) if recent else 0.0))
    ranking.sort(key=lambda item: item[1], reverse=True)
    labels = [market_label(market) for market, _ in ranking]
    values = [value for _, value in ranking]
    colors = ["#16a34a" if value > 0 else "#dc2626" if value < 0 else "#64748b" for value in values]
    ranking_ax.barh(labels, values, color=colors, height=0.58)
    ranking_ax.axvline(0, color="#94a3b8", linewidth=0.9)
    ranking_ax.set_title(f"Median anomaly score ({lookback_days} sessions)")
    ranking_ax.set_xlabel("GLOOM ← robust score → HOPE")
    ranking_ax.invert_yaxis()

    alignment_colors = {
        "HOPE": "#16a34a",
        "GLOOM": "#dc2626",
        "ALIGNED": "#2563eb",
        "QUIET": "#94a3b8",
    }
    for market, points in sorted(scored.items()):
        visible = points[-60:]
        x = [point.price_return for point in visible]
        y = [point.tone for point in visible]
        scatter_ax.scatter(
            x,
            y,
            s=22,
            alpha=0.55,
            color=[alignment_colors[point.alignment] for point in visible],
            label=market_label(market),
        )
        if market in residual_markets and len(visible) >= 3:
            slope, intercept, _, _ = theilslopes(y, x)
            line_x = np.asarray([min(x), max(x)])
            scatter_ax.plot(line_x, intercept + slope * line_x, linewidth=1.3)
        if visible:
            scatter_ax.annotate(
                market,
                (visible[-1].price_return, visible[-1].tone),
                xytext=(4, 4),
                textcoords="offset points",
                fontsize=8,
            )
    scatter_ax.axhline(0, color="#94a3b8", linewidth=0.8)
    scatter_ax.axvline(0, color="#94a3b8", linewidth=0.8)
    scatter_ax.set_title("Previous-session return vs pre-open tone")
    scatter_ax.set_xlabel("Previous close-to-close return (%)")
    scatter_ax.set_ylabel("Pre-open tone (-1..+1)")
    scatter_ax.grid(alpha=0.15)

    dates = sorted(
        {
            point.sentiment_for_session
            for market, points in scored.items()
            if market in residual_markets
            for point in points
            if point.anomaly_score is not None
        }
    )
    daily = {
        day: [
            point.anomaly_score
            for market, points in scored.items()
            if market in residual_markets
            for point in points
            if point.sentiment_for_session == day and point.anomaly_score is not None
        ]
        for day in dates
    }
    parsed_dates = [datetime.fromisoformat(day) for day in dates]
    base = [float(np.median(daily[day])) for day in dates]
    for window, color in ((7, "#0f766e"), (14, "#7c3aed"), (30, "#ea580c")):
        values = [
            float(np.median(base[max(0, index - window + 1) : index + 1]))
            for index in range(len(base))
        ]
        rolling_ax.plot(parsed_dates, values, label=f"{window} session", color=color)
    rolling_ax.axhline(0, color="#94a3b8", linewidth=0.8)
    rolling_ax.set_title("Rolling median anomaly strength across markets")
    rolling_ax.set_ylabel("robust score")
    rolling_ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    rolling_ax.tick_params(axis="x", rotation=45)
    rolling_ax.legend(frameon=False)
    rolling_ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()

    image = BytesIO()
    image.name = "market_anomaly.png"
    fig.savefig(image, format="png", dpi=160, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    image.seek(0)
    return image
