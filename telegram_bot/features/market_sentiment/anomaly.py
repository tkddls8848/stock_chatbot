"""전일 등락과 당일 개장 전 센티먼트의 정렬·아노말리 강도 계산."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
from scipy.stats import binomtest, median_abs_deviation, spearmanr, theilslopes

ROLLING_WINDOW = 60
MIN_EVALUATION_POINTS = 60
TONE_NEUTRAL_BAND = 0.10
EXTREME_THRESHOLD = 2.0
MAD_EPSILON = 1e-9


@dataclass(frozen=True)
class AnomalyPoint:
    price_session: str
    sentiment_for_session: str
    price_return: float
    tone: float
    forward: float
    expected_tone: float | None
    residual: float | None
    anomaly_score: float | None
    alignment: str
    strength: str
    article_count: int
    source_count: int
    window_hours: float


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_rows(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for entry in entries:
        price_return = _finite(entry.get("price_return"))
        tone = _finite(entry.get("tone"))
        if price_return is None or tone is None:
            continue
        rows.append({**entry, "price_return": price_return, "tone": tone})
    return sorted(rows, key=lambda row: str(row.get("price_session") or ""))


def _fit(history: list[dict[str, Any]]) -> tuple[float, float, np.ndarray, float]:
    returns = np.asarray([row["price_return"] for row in history], dtype=float)
    tones = np.asarray([row["tone"] for row in history], dtype=float)
    slope, intercept, _, _ = theilslopes(tones, returns)
    residuals = tones - (intercept + slope * returns)
    scale = float(median_abs_deviation(residuals, scale="normal"))
    return float(intercept), float(slope), residuals, scale


def _alignment(price_return: float, tone: float, quiet_floor: float) -> str:
    if abs(price_return) < quiet_floor or abs(tone) < TONE_NEUTRAL_BAND:
        return "QUIET"
    if price_return < 0 < tone:
        return "HOPE"
    if price_return > 0 > tone:
        return "GLOOM"
    return "ALIGNED"


def score_entries(
    entries: Iterable[dict[str, Any]],
    *,
    rolling_window: int = ROLLING_WINDOW,
) -> list[AnomalyPoint]:
    """Score every row strictly from rows preceding it."""
    rows = _valid_rows(entries)
    scored = []
    for index, row in enumerate(rows):
        history = rows[max(0, index - rolling_window) : index]
        expected = residual = anomaly_score = None
        quiet_floor = (
            float(np.quantile([abs(item["price_return"]) for item in history], 0.20))
            if history
            else 0.0
        )
        if len(history) >= rolling_window:
            intercept, slope, historical_residuals, scale = _fit(history)
            expected = intercept + slope * row["price_return"]
            residual = row["tone"] - expected
            if scale > MAD_EPSILON:
                anomaly_score = (
                    residual - float(np.median(historical_residuals))
                ) / scale
        alignment = _alignment(row["price_return"], row["tone"], quiet_floor)
        extreme = (
            anomaly_score is not None
            and (
                (alignment == "HOPE" and anomaly_score >= EXTREME_THRESHOLD)
                or (alignment == "GLOOM" and anomaly_score <= -EXTREME_THRESHOLD)
            )
        )
        scored.append(
            AnomalyPoint(
                price_session=str(row.get("price_session") or ""),
                sentiment_for_session=str(row.get("sentiment_for_session") or ""),
                price_return=row["price_return"],
                tone=row["tone"],
                forward=float(row.get("forward") or 0.0),
                expected_tone=expected,
                residual=residual,
                anomaly_score=anomaly_score,
                alignment=alignment,
                strength="EXTREME" if extreme else "ORDINARY",
                article_count=int(row.get("article_count") or 0),
                source_count=int(row.get("source_count") or 0),
                window_hours=float(row.get("window_hours") or 0.0),
            )
        )
    return scored


def _circular_block_pvalue(
    improvements: np.ndarray,
    *,
    block_size: int = 5,
    samples: int = 5000,
    seed: int = 20260821,
) -> float:
    """One-sided moving-block bootstrap p-value for mean improvement <= 0."""
    if improvements.size < block_size:
        return 1.0
    centered = improvements - improvements.mean()
    rng = np.random.default_rng(seed)
    means = np.empty(samples, dtype=float)
    block_count = math.ceil(improvements.size / block_size)
    offsets = np.arange(block_size)
    for sample in range(samples):
        starts = rng.integers(0, improvements.size, size=block_count)
        indexes = ((starts[:, None] + offsets) % improvements.size).ravel()
        means[sample] = centered[indexes[: improvements.size]].mean()
    return float((1 + np.count_nonzero(means >= improvements.mean())) / (samples + 1))


def market_gate_report(entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute G0~G7 inputs for one market; cross-market Holm is applied elsewhere."""
    source_rows = list(entries)
    rows = _valid_rows(source_rows)
    model_errors = []
    baseline_errors = []
    slopes = {}
    for index in range(ROLLING_WINDOW, len(rows)):
        history = rows[index - ROLLING_WINDOW : index]
        intercept, slope, _, _ = _fit(history)
        actual = rows[index]["tone"]
        model_errors.append(abs(actual - (intercept + slope * rows[index]["price_return"])))
        baseline_errors.append(abs(actual - float(np.median([r["tone"] for r in history]))))
    model_mae = float(np.mean(model_errors)) if model_errors else None
    baseline_mae = float(np.mean(baseline_errors)) if baseline_errors else None
    improvement = (
        1.0 - model_mae / baseline_mae
        if model_mae is not None and baseline_mae not in (None, 0.0)
        else None
    )
    paired_improvements = np.asarray(baseline_errors) - np.asarray(model_errors)
    pvalue = (
        _circular_block_pvalue(paired_improvements)
        if paired_improvements.size >= MIN_EVALUATION_POINTS
        else 1.0
    )
    for window in (60, 90, 120):
        if len(rows) >= window:
            x = [row["price_return"] for row in rows[-window:]]
            y = [row["tone"] for row in rows[-window:]]
            slope, _, low, high = theilslopes(y, x)
            slopes[str(window)] = {"slope": float(slope), "low": float(low), "high": float(high)}
    scored = score_entries(rows)
    evaluated = [point for point in scored if point.anomaly_score is not None]
    extreme = [point for point in evaluated if abs(point.anomaly_score or 0.0) >= EXTREME_THRESHOLD]
    positive_tail = sum((point.anomaly_score or 0.0) >= EXTREME_THRESHOLD for point in evaluated)
    negative_tail = sum((point.anomaly_score or 0.0) <= -EXTREME_THRESHOLD for point in evaluated)
    next_returns_extreme = []
    next_returns_normal = []
    for index, point in enumerate(scored[:-1]):
        if point.anomaly_score is None:
            continue
        target = next_returns_extreme if abs(point.anomaly_score) >= EXTREME_THRESHOLD else next_returns_normal
        target.append(scored[index + 1].price_return)
    normal_median = (
        float(np.median(next_returns_normal)) if next_returns_normal else None
    )
    if next_returns_extreme and normal_median is not None:
        non_ties = [value for value in next_returns_extreme if value != normal_median]
        positives = sum(value > normal_median for value in non_ties)
        g4_pvalue = (
            float(binomtest(positives, len(non_ties), 0.5).pvalue)
            if non_ties
            else 1.0
        )
    else:
        g4_pvalue = None
    reproducible = [row for row in rows if _finite(row.get("rescore_tone")) is not None]
    if len(reproducible) >= 30:
        original = [row["tone"] for row in reproducible]
        rescored = [float(row["rescore_tone"]) for row in reproducible]
        repeat_rho = float(spearmanr(original, rescored).statistic)
        repeat_delta = float(np.median(np.abs(np.asarray(original) - np.asarray(rescored))))
        label_matches = []
        for row in reproducible:
            index = rows.index(row)
            history = rows[max(0, index - ROLLING_WINDOW) : index]
            quiet_floor = (
                float(np.quantile([abs(item["price_return"]) for item in history], 0.20))
                if history
                else 0.0
            )
            label_matches.append(
                _alignment(row["price_return"], row["tone"], quiet_floor)
                == _alignment(
                    row["price_return"],
                    float(row["rescore_tone"]),
                    quiet_floor,
                )
            )
        repeat_label_agreement = sum(label_matches) / len(label_matches)
    else:
        repeat_rho = None
        repeat_delta = None
        repeat_label_agreement = None
    audited = [row for row in rows if isinstance(row.get("source_audit_match"), bool)]
    audit_match = (
        sum(bool(row["source_audit_match"]) for row in audited) / len(audited)
        if audited
        else None
    )
    dense = [
        row
        for row in source_rows
        if int(row.get("article_count") or 0) >= 8
        and int(row.get("source_count") or 0) >= 4
    ]
    density = len(dense) / len(source_rows) if source_rows else 0.0
    signs = [item["slope"] for item in slopes.values()]
    g2 = bool(
        len(signs) == 3
        and all(value > 0 for value in signs) or len(signs) == 3 and all(value < 0 for value in signs)
    )
    if "90" in slopes:
        g2 = g2 and not (slopes["90"]["low"] <= 0 <= slopes["90"]["high"])
    return {
        "samples": len(rows),
        "attempted_samples": len(source_rows),
        "evaluation_samples": len(model_errors),
        "spearman": (
            float(spearmanr([r["price_return"] for r in rows], [r["tone"] for r in rows]).statistic)
            if len(rows) >= 3
            else None
        ),
        "model_mae": model_mae,
        "baseline_mae": baseline_mae,
        "improvement": improvement,
        "g0_raw_pvalue": pvalue,
        "g1": len(rows) >= 120 and len(model_errors) >= MIN_EVALUATION_POINTS,
        "slopes": slopes,
        "g2": g2,
        "extreme_ratio": len(extreme) / len(evaluated) if evaluated else None,
        "g3": bool(evaluated and 0.01 <= len(extreme) / len(evaluated) <= 0.20 and positive_tail and negative_tail),
        "g4": {
            "extreme_samples": len(next_returns_extreme),
            "normal_samples": len(next_returns_normal),
            "extreme_next_return_median": (
                float(np.median(next_returns_extreme))
                if next_returns_extreme
                else None
            ),
            "normal_next_return_median": normal_median,
            "sign_pvalue": g4_pvalue,
        },
        "repeat_samples": len(reproducible),
        "repeat_rho": repeat_rho,
        "repeat_median_delta": repeat_delta,
        "repeat_label_agreement": repeat_label_agreement,
        "g6": bool(
            repeat_rho is not None
            and repeat_rho >= 0.80
            and repeat_delta is not None
            and repeat_delta <= 0.10
            and repeat_label_agreement is not None
            and repeat_label_agreement >= 0.80
        ),
        "dense_ratio": density,
        "audit_samples": len(audited),
        "audit_match_ratio": audit_match,
        "g7": bool(density >= 0.70 and len(audited) >= 30 and audit_match is not None and audit_match >= 0.70),
    }


def apply_holm(reports: dict[str, dict[str, Any]], alpha: float = 0.05) -> None:
    """Mutate reports with Holm-adjusted G0 decisions across markets."""
    ordered = sorted(reports.items(), key=lambda item: item[1]["g0_raw_pvalue"])
    still_rejecting = True
    total = len(ordered)
    for index, (_, report) in enumerate(ordered):
        threshold = alpha / (total - index)
        passed = bool(
            still_rejecting
            and report["g1"]
            and report["improvement"] is not None
            and report["improvement"] >= 0.10
            and report["g0_raw_pvalue"] <= threshold
        )
        report["g0_holm_threshold"] = threshold
        report["g0"] = passed
        if not passed:
            still_rejecting = False
