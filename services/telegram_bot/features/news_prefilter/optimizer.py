"""원문 헤드라인 impact 모델의 CPU-budgeted walk-forward 학습."""

from __future__ import annotations

import hashlib
import math
import os
import random
import re
import threading
import time
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, Generator

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
MAX_SEARCH_TRIALS = 32
MIN_TRAINING_LABELS = 120
MIN_TRAINING_DAYS = 3
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
    # 같은 점수는 하나의 threshold로 묶는다. 상수 모델의 AP는 기저 비율이다.
    ranked = sorted(range(len(labels)), key=scores.__getitem__, reverse=True)
    hits = 0
    total = 0.0
    start = 0
    while start < len(ranked):
        end = start + 1
        while end < len(ranked) and scores[ranked[end]] == scores[ranked[start]]:
            end += 1
        group_hits = sum(labels[index] for index in ranked[start:end])
        hits += group_hits
        total += group_hits * hits / end
        start = end
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
) -> Generator[None, None, tuple[float, float, list[float]]]:
    weights = [0.0] * MODEL_DIMENSIONS
    intercept = 0.0
    learning_rate = rng.choice((0.02, 0.035, 0.05))
    regularization = rng.choice((1e-5, 5e-5, 1e-4))
    positive_weight = max(1.0, (len(train_labels) - sum(train_labels)) / max(1, sum(train_labels)))

    indices = list(range(len(train_labels)))
    for _ in range(rng.choice((4, 6, 8))):
        rng.shuffle(indices)
        for position, index in enumerate(indices):
            if position % 8 == 0:
                yield
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
    *,
    state: dict[str, Any] | None = None,
) -> OptimizationResult:
    """한 trial도 CPU 조각 사이에서 이어 학습한다. 자료당 최대 32회 검증한다.

    마지막 날짜는 학습에서 제외한다. 이는 모델 선택용 검증이며, 실제 수집
    개선의 증거는 active 탐색 기사가 후속 보고서에서 받은 평가로 따로 본다.
    """
    _lower_current_thread_priority()
    started = time.thread_time()
    deadline = started + max(0.0, float(cpu_budget_seconds))
    if state is None:
        state = {}
    days = sorted({sample.day for sample in samples if sample.day})
    if len(samples) < MIN_TRAINING_LABELS or len(days) < MIN_TRAINING_DAYS:
        state.clear()
        return OptimizationResult(time.thread_time() - started, 0, len(samples), None,
                                  "insufficient_labels")
    # 라벨 수가 같아도 보존 창 이동이나 평가 내용 변경이면 새 검색을 시작한다.
    fingerprint = hashlib.sha256(repr(samples).encode("utf-8")).hexdigest()
    if state.get("fingerprint") != fingerprint:
        state.clear()
        test_days = set(days[-max(1, len(days) // 5):])
        train = [sample for sample in samples if sample.day not in test_days]
        test = [sample for sample in samples if sample.day in test_days]
        # 양성이 대부분인 보고서 근거만으로 만든 모델을 승격시키지 않는다.
        if (min(sum(s.label == label for s in train) for label in (0, 1)) < 10
                or min(sum(s.label == label for s in test) for label in (0, 1)) < 5):
            return OptimizationResult(time.thread_time() - started, 0, len(samples), None,
                                      "invalid_time_split")
        train_vectors = [_vector(sample.title, sample.features) for sample in train]
        test_vectors = [_vector(sample.title, sample.features) for sample in test]
        train_labels = [sample.label for sample in train]
        test_labels = [sample.label for sample in test]
        base = sum(test_labels) / len(test_labels)
        incumbent = _incumbent_average_precision(current_model, test_vectors, test_labels)
        state.update(
            fingerprint=fingerprint, train_vectors=train_vectors, test_vectors=test_vectors,
            train_labels=train_labels, test_labels=test_labels,
            rng=random.Random(fingerprint), trials=0, best_ap=max(base, incumbent),
            test_days=sorted(test_days), train_days=sorted(set(days) - test_days),
            prevalence=base,
        )
    trials = 0
    best_model = None
    while time.thread_time() < deadline and state["trials"] < MAX_SEARCH_TRIALS:
        if "trial" not in state:
            state["trial"] = _fit_trial(
                state["train_vectors"], state["train_labels"],
                state["test_vectors"], state["test_labels"], state["rng"],
            )
        try:
            next(state["trial"])
        except StopIteration as completed:
            del state["trial"]
            validation_ap, intercept, weights = completed.value
            trials += 1
            state["trials"] += 1
            if validation_ap <= state["best_ap"]:
                continue
            state["best_ap"] = validation_ap
            best_model = {
                "version": 1, "feature_names": list(FEATURE_NAMES),
                "hash_buckets": HASH_BUCKETS, "intercept": round(intercept, 8),
                "weights": [round(weight, 8) for weight in weights],
                "validation_ap": round(validation_ap, 6),
                "validation_prevalence": round(state["prevalence"], 6),
                "label_count": len(samples), "train_days": state["train_days"],
                "test_days": state["test_days"],
            }
    reason = "search_complete" if state["trials"] >= MAX_SEARCH_TRIALS else ""
    return OptimizationResult(time.thread_time() - started, trials, len(samples), best_model, reason)
