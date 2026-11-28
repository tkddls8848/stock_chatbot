"""사전선별 임베딩 one-shot — `python -m services.telegram_bot.features.news_prefilter.embed_job`.

매시간 timer가 띄우고 끝나면 내려간다(`infra/systemd/stock-chatbot-prefilter-embed.*`). 봇 유닛은
`MemoryMax=1G`라 bge-m3(실측 1.05GB)를 봇 안에 올릴 수 없어 자기 cgroup의 프로세스로 뗐다.
계획서: `services/telegram_bot/docs/prefilter-burst-credit.md` 2·3단계.

- **매시간(작은 몰아 쓰기)**: 사건 메모리의 새 사건만 임베딩해 벡터 저장소에 더하고, simhash가 따로 둔
  사건 쌍 중 임베딩으로는 같아 보이는 쌍을 섀도로 남긴다. 판정에는 아직 쓰지 않는다 — 운영자가 표본을
  보고 문턱을 정한 뒤 바꾼다.
- **매일 한 번(큰 몰아 쓰기)**: 라벨 붙은 관측 제목을 임베딩하고, 같은 시간 분할·같은 선택 절차로
  지금 모델(v1, 해시 n-gram)과 임베딩 모델(v2)을 다시 학습해 holdout AP를 비교한다. 결과는 판단 자료일
  뿐 모델 파일을 바꾸지 않는다.

시작할 때와 묶음 사이마다 버스트 크레딧 조절기(`host_cpu.py`, 읽기 전용)를 본다. 메모리가 모자라거나
크레딧 잔고가 하한 아래면 모델을 올리지 않고 0으로 끝난다. 쓰는 파일은 `NEWS_PREFILTER_EMBED_DIR`
아래뿐이다 — 사건 메모리·관측·조절기 표본은 봇이 쓰고 여기서는 읽기만 한다.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import resource
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np

from services.telegram_bot.core.clock import now
from services.telegram_bot.core.config import (
    NEWS_PREFILTER_BURST_BASELINE,
    NEWS_PREFILTER_BURST_FLOOR_PERCENT,
    NEWS_PREFILTER_EMBED_DAILY_HOUR,
    NEWS_PREFILTER_EMBED_DIR,
    NEWS_PREFILTER_EMBED_MIN_AVAILABLE_MB,
    NEWS_PREFILTER_EMBED_MODEL_DIR,
    NEWS_PREFILTER_EMBED_SHADOW_MIN_SIMILARITY,
    NEWS_PREFILTER_EVENT_FILE,
    NEWS_PREFILTER_HOST_CPU_FILE,
    NEWS_PREFILTER_HOST_STEAL_LIMIT,
    NEWS_PREFILTER_OBSERVATION_FILE,
    NEWS_PREFILTER_OBSERVATION_RETENTION_DAYS,
)
from services.telegram_bot.core.storage import write_bytes_atomic, write_json_atomic
from services.telegram_bot.features.news_prefilter.host_cpu import HostCpuBudget
from services.telegram_bot.features.news_prefilter.learning import ObservationLearner
from services.telegram_bot.features.news_prefilter.optimizer import (
    FEATURE_NAMES,
    TrainingSample,
    _average_precision,
    _normalized_title,
    inner_folds,
    optimize_for_cpu_budget,
)

logger = logging.getLogger(__name__)

STATUS_FILE = "status.json"
SHADOW_FILE = "shadow.json"
_EVENT_VECTORS = "event_vectors.npz"
_TITLE_VECTORS = "title_vectors.npz"
_RECAP_PREFIX = "recap|"  # 장 시황 묶음 열쇠(service._RECAP_EVENT_PREFIX). 문장이 아니라 임베딩하지 않는다
_BATCH = 16
_GOVERNOR_EVERY_SECONDS = 30.0
_SHADOW_RETENTION_DAYS = 7
_SHADOW_MAX_PAIRS = 300
_SHADOW_THRESHOLDS = (0.70, 0.75, 0.80, 0.85)
_V2_REGULARIZATIONS = (1e-4, 1e-3, 1e-2, 1e-1)


class Stop(Exception):
    """조절기가 멈추라고 했다. 그때까지 한 일은 저장한다."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def available_memory_mb(path: Path = Path("/proc/meminfo")) -> float | None:
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _max_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


# ── 벡터 저장소 ─────────────────────────────────────────

def _load_vectors(path: Path) -> dict[str, np.ndarray]:
    try:
        with np.load(path, allow_pickle=False) as data:
            keys = [str(key) for key in data["keys"]]
            matrix = data["vectors"].astype(np.float32)
    except (OSError, KeyError, ValueError):
        return {}
    if len(keys) != len(matrix):
        return {}
    return dict(zip(keys, matrix))


def _save_vectors(path: Path, vectors: dict[str, np.ndarray]) -> None:
    keys = list(vectors)
    matrix = np.stack([vectors[key] for key in keys]).astype(np.float16) if keys else np.zeros((0, 0), np.float16)
    buffer = io.BytesIO()
    np.savez(buffer, keys=np.array(keys, dtype=str), vectors=matrix)
    write_bytes_atomic(path, buffer.getvalue())


def _embed_missing(
    encode: Callable[[list[str]], np.ndarray],
    items: list[tuple[str, str]],
    store: dict[str, np.ndarray],
    governor: Callable[[], str],
) -> int:
    """(key, text) 중 저장소에 없는 것을 묶음으로 임베딩한다. 조절기가 멈추면 Stop."""
    missing = [(key, text) for key, text in items if key not in store]
    checked = time.monotonic()
    for start in range(0, len(missing), _BATCH):
        if time.monotonic() - checked >= _GOVERNOR_EVERY_SECONDS:
            checked = time.monotonic()
            reason = governor()
            if reason:
                raise Stop(reason)
        chunk = missing[start : start + _BATCH]
        for (key, _), vector in zip(chunk, encode([text for _, text in chunk])):
            store[key] = vector
    return len(missing)


# ── 매시간: 사건 임베딩과 섀도 군집 ──────────────────────

def _load_events(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    events = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text, event_id = str(item.get("text") or ""), str(item.get("event_id") or "")
        if event_id and text and not text.startswith(_RECAP_PREFIX):
            events.append({"id": event_id, "text": text, "first_seen": str(item.get("first_seen") or "")})
    return events


def shadow_pairs(
    events: list[dict[str, Any]],
    vectors: dict[str, np.ndarray],
    new_ids: set[str],
    min_similarity: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """새 사건마다 먼저 본 사건 중 가장 가까운 것을 찾는다. simhash는 이 둘을 다른 사건으로 두었다."""
    ordered = sorted((e for e in events if e["id"] in vectors), key=lambda e: (e["first_seen"], e["id"]))
    if not ordered:
        return [], {}
    matrix = np.stack([vectors[e["id"]] for e in ordered])
    pairs: list[dict[str, Any]] = []
    days: dict[str, dict[str, int]] = {}
    for index, event in enumerate(ordered):
        if event["id"] not in new_ids:
            continue
        day = days.setdefault(event["first_seen"][:10], {"new": 0, **{f"{t:.2f}": 0 for t in _SHADOW_THRESHOLDS}})
        day["new"] += 1
        if index == 0:
            continue
        sims = matrix[:index] @ matrix[index]
        best = int(np.argmax(sims))
        similarity = float(sims[best])
        for threshold in _SHADOW_THRESHOLDS:
            if similarity >= threshold:
                day[f"{threshold:.2f}"] += 1
        if similarity >= min_similarity:
            pairs.append({
                "at": event["first_seen"], "similarity": round(similarity, 4),
                "new": event["text"][:160], "earlier": ordered[best]["text"][:160],
                "earlier_at": ordered[best]["first_seen"],
            })
    return pairs, days


def _merge_shadow(previous: dict[str, Any], pairs, days, min_similarity: float) -> dict[str, Any]:
    floor = (now() - timedelta(days=_SHADOW_RETENTION_DAYS)).date().isoformat()
    merged_days = {d: v for d, v in (previous.get("days") or {}).items() if d >= floor}
    for day, counts in days.items():
        if day < floor:
            continue
        target = merged_days.setdefault(day, {key: 0 for key in counts})
        for key, value in counts.items():
            target[key] = target.get(key, 0) + value
    kept = [p for p in previous.get("pairs") or [] if str(p.get("at", ""))[:10] >= floor]
    all_pairs = sorted([*kept, *pairs], key=lambda p: str(p.get("at", "")), reverse=True)
    return {
        "updated_at": now().isoformat(timespec="seconds"),
        "min_similarity": min_similarity,
        "days": dict(sorted(merged_days.items())),
        "pairs": all_pairs[:_SHADOW_MAX_PAIRS],
    }


def run_hourly(encode, governor, *, event_file: Path, embed_dir: Path, min_similarity: float) -> dict[str, Any]:
    events = _load_events(event_file)
    path = embed_dir / _EVENT_VECTORS
    store = _load_vectors(path)
    live = {e["id"] for e in events}
    store = {key: value for key, value in store.items() if key in live}
    before = set(store)
    stopped = ""
    try:
        _embed_missing(encode, [(e["id"], e["text"]) for e in events], store, governor)
    except Stop as stop:
        stopped = stop.reason
    new_ids = set(store) - before
    _save_vectors(path, store)
    pairs, days = shadow_pairs(events, store, new_ids, min_similarity)
    shadow_path = embed_dir / SHADOW_FILE
    try:
        previous = json.loads(shadow_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}
    write_json_atomic(shadow_path, _merge_shadow(previous if isinstance(previous, dict) else {}, pairs, days,
                                                 min_similarity))
    return {"events": len(store), "embedded": len(new_ids), "shadow_pairs": len(pairs), "stopped": stopped}


# ── 매일: v1·v2 오프라인 비교 ────────────────────────────

def _title_key(title: str) -> str:
    return hashlib.sha1(_normalized_title(title).encode("utf-8", errors="replace")).hexdigest()


def _fit_logistic(x: np.ndarray, y: np.ndarray, regularization: float, iterations: int = 300) -> np.ndarray:
    """클래스 가중 L2 로지스틱(전체 배치 경사하강). 마지막 열이 절편이다."""
    design = np.hstack([x, np.ones((len(x), 1), dtype=np.float32)])
    positive_weight = max(1.0, float((y == 0).sum()) / max(1.0, float(y.sum())))
    sample_weight = np.where(y == 1, positive_weight, 1.0)
    weights = np.zeros(design.shape[1], dtype=np.float64)
    for _ in range(iterations):
        probability = 1 / (1 + np.exp(-np.clip(design @ weights, -40, 40)))
        gradient = design.T @ ((probability - y) * sample_weight) / len(y)
        gradient[:-1] += regularization * weights[:-1]
        weights -= 0.5 * gradient
    return weights


def _predict(x: np.ndarray, weights: np.ndarray) -> list[float]:
    design = np.hstack([x, np.ones((len(x), 1), dtype=np.float32)])
    return list(1 / (1 + np.exp(-np.clip(design @ weights, -40, 40))))


def compare_models(samples: list[TrainingSample], title_vectors: dict[str, np.ndarray]) -> dict[str, Any] | None:
    """같은 holdout·같은 선택 절차(내부 walk-forward)로 v1과 v2를 처음부터 학습해 비교한다."""
    samples = [s for s in samples if _title_key(s.title) in title_vectors]
    days = sorted({s.day for s in samples if s.day})
    if len(samples) < 120 or len(days) < 3:
        return None
    test_days = set(days[-max(1, len(days) // 5):])
    sample_days = [s.day for s in samples]
    labels = [s.label for s in samples]
    train = [i for i, d in enumerate(sample_days) if d not in test_days]
    test = [i for i, d in enumerate(sample_days) if d in test_days]
    test_labels = [labels[i] for i in test]
    if not 0 < sum(test_labels) < len(test_labels):
        return None
    base = sum(test_labels) / len(test_labels)

    state: dict[str, Any] = {}
    v1_ap = base
    while True:
        result = optimize_for_cpu_budget(samples, None, 60.0, state=state)
        if result.model is not None:
            v1_ap = float(result.model["validation_ap"])
        if result.reason or not result.trials:
            break

    x = np.stack([
        np.concatenate([
            np.array([float(s.features.get(name, 0.0)) for name in FEATURE_NAMES], dtype=np.float32),
            title_vectors[_title_key(s.title)],
        ]) for s in samples
    ])
    y = np.array(labels, dtype=np.float64)
    folds = inner_folds(train, sample_days, labels)
    best_reg, best_inner = _V2_REGULARIZATIONS[0], -1.0
    for regularization in _V2_REGULARIZATIONS:
        scores = [
            _average_precision([labels[i] for i in fold_test],
                               _predict(x[fold_test], _fit_logistic(x[fold_train], y[fold_train], regularization)))
            for fold_train, fold_test in folds
        ]
        inner = sum(scores) / len(scores) if scores else 0.0
        if inner > best_inner:
            best_reg, best_inner = regularization, inner
    weights = _fit_logistic(x[train], y[train], best_reg)
    v2_ap = _average_precision(test_labels, _predict(x[test], weights))
    return {
        "labels": len(samples), "test_days": sorted(test_days), "prevalence": round(base, 4),
        "v1_ap": round(v1_ap, 4), "v2_ap": round(float(v2_ap), 4),
        "v2_inner_ap": round(best_inner, 4), "v2_regularization": best_reg, "inner_folds": len(folds),
    }


def run_daily(encode, governor, *, observation_file: Path, embed_dir: Path) -> dict[str, Any]:
    learner = ObservationLearner(
        observation_file, NEWS_PREFILTER_OBSERVATION_RETENTION_DAYS, threading.RLock(), compact=False,
    )
    samples = list(learner.load_training_samples())
    path = embed_dir / _TITLE_VECTORS
    store = _load_vectors(path)
    wanted = {_title_key(s.title): s.title for s in samples}
    store = {key: value for key, value in store.items() if key in wanted}
    try:
        embedded = _embed_missing(encode, list(wanted.items()), store, governor)
    finally:
        _save_vectors(path, store)
    reason = governor()
    if reason:
        raise Stop(reason)
    return {"titles": len(store), "embedded": embedded, "comparison": compare_models(samples, store)}


# ── 실행 ────────────────────────────────────────────────

def _daily_due(status: dict[str, Any], moment: datetime) -> bool:
    return moment.hour >= NEWS_PREFILTER_EMBED_DAILY_HOUR and status.get("daily_date") != moment.date().isoformat()


def run(
    *,
    embed_dir: Path = NEWS_PREFILTER_EMBED_DIR,
    model_dir: Path = NEWS_PREFILTER_EMBED_MODEL_DIR,
    event_file: Path = NEWS_PREFILTER_EVENT_FILE,
    observation_file: Path = NEWS_PREFILTER_OBSERVATION_FILE,
    budget: HostCpuBudget | None = None,
    encoder_factory: Callable[[Path], Callable[[list[str]], np.ndarray]] | None = None,
    memory_mb: Callable[[], float | None] = available_memory_mb,
) -> dict[str, Any]:
    started_cpu = time.process_time()
    moment = now()
    status_path = embed_dir / STATUS_FILE
    try:
        previous = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}
    previous = previous if isinstance(previous, dict) else {}
    status: dict[str, Any] = {
        "at": moment.isoformat(timespec="seconds"),
        "daily_date": previous.get("daily_date", ""),
        "daily": previous.get("daily"),
    }
    if budget is None:
        budget = HostCpuBudget(
            NEWS_PREFILTER_HOST_CPU_FILE, baseline=NEWS_PREFILTER_BURST_BASELINE,
            floor_percent=NEWS_PREFILTER_BURST_FLOOR_PERCENT,
            steal_limit=NEWS_PREFILTER_HOST_STEAL_LIMIT, persist=False,
        )

    def governor() -> str:
        verdict = budget.check()
        if verdict is not None:
            status["balance_percent"] = verdict.balance_percent
        return verdict.reason if verdict is not None else ""

    def finish(result: str) -> dict[str, Any]:
        status.update(result=result, cpu_seconds=round(time.process_time() - started_cpu, 1),
                      max_rss_mb=round(_max_rss_mb()))
        write_json_atomic(status_path, status)
        logger.info("[PREFILTER-EMBED] %s", json.dumps(status, ensure_ascii=False))
        return status

    if encoder_factory is None and not (model_dir / "model.onnx").exists():
        return finish("model_missing")
    available = memory_mb()
    status["available_mb"] = None if available is None else round(available)
    if available is not None and available < NEWS_PREFILTER_EMBED_MIN_AVAILABLE_MB:
        return finish("memory_low")
    reason = governor()
    if reason:
        return finish(reason)

    if encoder_factory is None:
        from services.telegram_bot.features.news_prefilter.embedding import Encoder

        encode = Encoder(model_dir).encode
    else:
        encode = encoder_factory(model_dir)
    hourly = run_hourly(encode, governor, event_file=event_file, embed_dir=embed_dir,
                        min_similarity=NEWS_PREFILTER_EMBED_SHADOW_MIN_SIMILARITY)
    status["hourly"] = hourly
    if hourly["stopped"]:
        return finish(hourly["stopped"])
    if _daily_due(previous, moment):
        try:
            daily = run_daily(encode, governor, observation_file=observation_file, embed_dir=embed_dir)
        except Stop as stop:
            return finish(stop.reason)
        status["daily"] = daily
        status["daily_date"] = moment.date().isoformat()
    return finish("ok")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    NEWS_PREFILTER_EMBED_DIR.mkdir(parents=True, exist_ok=True)
    run()


if __name__ == "__main__":
    main()
