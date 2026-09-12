"""원문 헤드라인 impact 모델의 CPU-budgeted walk-forward 학습."""

from __future__ import annotations

import math
import os
import random
import re
import threading
import time
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

FEATURE_NAMES = (
    "freshness",
    "watchlist_hit",
    "entity_density",
    "novelty",
    "source_diversity",
    "headline_signal",
    "duplicate_similarity",
)
HASH_BUCKETS = 2048
MODEL_DIMENSIONS = len(FEATURE_NAMES) + HASH_BUCKETS
_TEXT_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class TrainingSample:
    day: str
    title: str
    features: dict[str, float]
    label: int


@dataclass(frozen=True)
class OptimizationResult:
    cpu_seconds: float
    trials: int
    label_count: int
    model: dict[str, Any] | None
    reason: str = ""


def _lower_current_thread_priority() -> None:
    """Linux에서는 이 전용 worker thread만 nice 15로 낮춘다."""
    if not hasattr(os, "setpriority") or not hasattr(os, "PRIO_PROCESS"):
        return
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 15)
    except (OSError, PermissionError):
        pass


def _normalized_title(title: str) -> str:
    return _TEXT_RE.sub(" ", str(title or "").strip().lower())[:240]


def _vector(title: str, features: dict[str, float]) -> tuple[tuple[int, float], ...]:
    values: list[tuple[int, float]] = [
        (index, float(features.get(name, 0.0)))
        for index, name in enumerate(FEATURE_NAMES)
    ]
    text = _normalized_title(title)
    counts: Counter[int] = Counter()
    padded = f" {text} "
    for size in (3, 4, 5):
        for start in range(max(0, len(padded) - size + 1)):
            gram = padded[start : start + size].encode("utf-8")
            bucket = zlib.crc32(gram) % HASH_BUCKETS
            counts[len(FEATURE_NAMES) + bucket] += 1
    norm = math.sqrt(sum(count * count for count in counts.values())) or 1.0
    values.extend((index, count / norm) for index, count in counts.items())
    return tuple(values)


def _sigmoid(value: float) -> float:
    if value >= 0:
        exp = math.exp(-min(value, 40.0))
        return 1.0 / (1.0 + exp)
    exp = math.exp(max(value, -40.0))
    return exp / (1.0 + exp)


def predict_probability(
    model: dict[str, Any] | None,
    title: str,
    features: dict[str, float],
) -> float:
    if not model:
        return 0.5
    raw_weights = model.get("weights")
    if not isinstance(raw_weights, list) or len(raw_weights) != MODEL_DIMENSIONS:
        return 0.5
    weights = raw_weights
    score = float(model.get("intercept") or 0.0)
    for index, value in _vector(title, features):
        score += float(weights[index]) * value
    return _sigmoid(score)


def _average_precision(labels: list[int], scores: list[float]) -> float:
    positives = sum(labels)
    if positives <= 0:
        return 0.0
    ranked = sorted(range(len(labels)), key=scores.__getitem__, reverse=True)
    hits = 0
    total = 0.0
    for rank, index in enumerate(ranked, 1):
        if labels[index]:
            hits += 1
            total += hits / rank
    return total / positives


def _incumbent_average_precision(
    model: dict[str, Any] | None,
    test_vectors: list[tuple[tuple[int, float], ...]],
    test_labels: list[int],
) -> float:
    """현재 모델을 이번 test split에서 다시 재 기준선으로 삼는다."""
    if not model:
        return 0.0
    weights = model.get("weights")
    if not isinstance(weights, list) or len(weights) != MODEL_DIMENSIONS:
        return 0.0
    intercept = float(model.get("intercept") or 0.0)
    scores = [
        _sigmoid(intercept + sum(float(weights[i]) * value for i, value in vector))
        for vector in test_vectors
    ]
    return _average_precision(test_labels, scores)


def _fit_trial(
    train_vectors: list[tuple[tuple[int, float], ...]],
    train_labels: list[int],
    test_vectors: list[tuple[tuple[int, float], ...]],
    test_labels: list[int],
    rng: random.Random,
) -> tuple[float, float, list[float]]:
    weights = [0.0] * MODEL_DIMENSIONS
    intercept = 0.0
    learning_rate = rng.choice((0.02, 0.035, 0.05))
    regularization = rng.choice((1e-5, 5e-5, 1e-4))
    positive_weight = max(1.0, (len(train_labels) - sum(train_labels)) / max(1, sum(train_labels)))

    indices = list(range(len(train_labels)))
    for _ in range(rng.choice((4, 6, 8))):
        rng.shuffle(indices)
        for index in indices:
            vector = train_vectors[index]
            label = train_labels[index]
            score = intercept + sum(weights[i] * value for i, value in vector)
            error = (label - _sigmoid(score)) * (positive_weight if label else 1.0)
            intercept += learning_rate * error
            for feature_index, value in vector:
                weight = weights[feature_index]
                weights[feature_index] = weight + learning_rate * (
                    error * value - regularization * weight
                )
        learning_rate *= 0.85

    scores = [
        _sigmoid(intercept + sum(weights[i] * value for i, value in vector))
        for vector in test_vectors
    ]
    return _average_precision(test_labels, scores), intercept, weights


def optimize_for_cpu_budget(
    samples: list[TrainingSample],
    current_model: dict[str, Any] | None,
    cpu_budget_seconds: float,
) -> OptimizationResult:
    """주어진 thread CPU-time 안에서 여러 초기값을 walk-forward 검증한다."""
    _lower_current_thread_priority()
    started = time.thread_time()
    days = sorted({sample.day for sample in samples if sample.day})
    if len(samples) < 120 or len(days) < 5:
        return OptimizationResult(
            time.thread_time() - started,
            0,
            len(samples),
            None,
            "insufficient_labels",
        )

    test_day_count = max(1, len(days) // 5)
    test_days = set(days[-test_day_count:])
    train = [sample for sample in samples if sample.day not in test_days]
    test = [sample for sample in samples if sample.day in test_days]
    if not train or not test or len({sample.label for sample in test}) < 2:
        return OptimizationResult(
            time.thread_time() - started,
            0,
            len(samples),
            None,
            "invalid_time_split",
        )

    train_vectors = [_vector(sample.title, sample.features) for sample in train]
    test_vectors = [_vector(sample.title, sample.features) for sample in test]
    train_labels = [sample.label for sample in train]
    test_labels = [sample.label for sample in test]
    deadline = started + max(0.05, float(cpu_budget_seconds))
    rng = random.Random(time.time_ns())
    # 기존 모델의 저장된 AP를 그대로 기준선으로 쓰지 않는다. 날짜가 쌓이면
    # walk-forward split이 달라져 다른 test set에서 잰 값이 되고, 우연히 쉬운
    # split에서 높게 나온 값 하나가 이후 갱신을 영구히 막는다. 같은 split에서
    # 다시 재서 비교한다.
    best_ap = _incumbent_average_precision(current_model, test_vectors, test_labels)
    best_model: dict[str, Any] | None = None
    trials = 0

    while time.thread_time() < deadline:
        validation_ap, intercept, weights = _fit_trial(
            train_vectors,
            train_labels,
            test_vectors,
            test_labels,
            rng,
        )
        trials += 1
        if validation_ap <= best_ap:
            continue
        best_ap = validation_ap
        best_model = {
            "version": 1,
            "feature_names": list(FEATURE_NAMES),
            "hash_buckets": HASH_BUCKETS,
            "intercept": round(intercept, 8),
            "weights": [round(weight, 8) for weight in weights],
            "validation_ap": round(validation_ap, 6),
            "validation_prevalence": round(sum(test_labels) / len(test_labels), 6),
            "label_count": len(samples),
            "train_days": sorted(set(days) - test_days),
            "test_days": sorted(test_days),
        }

    return OptimizationResult(
        time.thread_time() - started,
        trials,
        len(samples),
        best_model,
    )

