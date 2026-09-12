"""Incremental observation-file reader for prefilter model training."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from telegram_bot.features.news_prefilter.optimizer import TrainingSample

_MAX_TRAINING_SAMPLES = 50000
PENDING_CANDIDATE_LIMIT = 20000
_PENDING_CANDIDATE_LIMIT = PENDING_CANDIDATE_LIMIT


class ObservationLearner:
    def __init__(
        self,
        observation_file: Path,
        retention_days: int,
        file_lock: threading.RLock,
    ):
        self._observation_file = observation_file
        self._observation_retention_days = max(1, retention_days)
        self._file_lock = file_lock
        self._training_samples: list[TrainingSample] = []
        self._pending_candidates: dict[str, tuple[str, str, dict[str, Any]]] = {}
        self._samples_offset = 0
        self._last_compact_day = ""

    def _compact_observations_if_needed(self) -> bool:
        """보존 기간이 지난 관측을 하루 한 번 걷어낸다. 재작성했으면 True."""
        today = datetime.now(timezone.utc).date().isoformat()
        if self._last_compact_day == today or not self._observation_file.exists():
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self._observation_retention_days
        )
        temporary = self._observation_file.with_name(
            f"{self._observation_file.name}.{os.getpid()}.compact.tmp"
        )
        with self._file_lock:
            try:
                with self._observation_file.open("r", encoding="utf-8") as source, temporary.open(
                    "w", encoding="utf-8", newline="\n"
                ) as target:
                    for line in source:
                        try:
                            item = json.loads(line)
                            observed = datetime.fromisoformat(str(item.get("observed_at")))
                            if observed.tzinfo is None:
                                observed = observed.replace(tzinfo=timezone.utc)
                            if observed.astimezone(timezone.utc) < cutoff:
                                continue
                        except (ValueError, TypeError, json.JSONDecodeError):
                            continue
                        target.write(line if line.endswith("\n") else line + "\n")
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self._observation_file)
            finally:
                temporary.unlink(missing_ok=True)
        self._last_compact_day = today
        return True

    def _reset_sample_state(self) -> None:
        self._training_samples = []
        self._pending_candidates = {}
        self._samples_offset = 0

    def _consume_observation(self, item: dict[str, Any]) -> None:
        """관측 한 줄을 학습 샘플 상태에 반영한다."""
        candidate_id = str(item.get("candidate_id") or "")
        if not candidate_id:
            return
        kind = item.get("type")
        if kind == "candidate":
            features = item.get("features")
            if not isinstance(features, dict):
                return
            self._pending_candidates[candidate_id] = (
                str(item.get("observed_at") or "")[:10],
                str(item.get("title") or ""),
                features,
            )
            while len(self._pending_candidates) > _PENDING_CANDIDATE_LIMIT:
                self._pending_candidates.pop(next(iter(self._pending_candidates)))
            return
        if kind != "outcome":
            return
        pending = self._pending_candidates.pop(candidate_id, None)
        if pending is None:
            return
        impact = str(item.get("impact") or "")
        if impact not in {"high", "medium", "low"}:
            return
        day, title, features = pending
        try:
            values = {key: float(value) for key, value in features.items()}
        except (TypeError, ValueError):
            return
        self._training_samples.append(
            TrainingSample(
                day=day,
                title=title,
                features=values,
                label=int(impact in {"high", "medium"}),
            )
        )
        if len(self._training_samples) > _MAX_TRAINING_SAMPLES:
            del self._training_samples[:-_MAX_TRAINING_SAMPLES]

    def load_training_samples(self) -> list[TrainingSample]:
        """지난 호출 이후 덧붙은 관측만 이어 읽는다.

        관측 파일은 append 전용이라 offset 뒤만 읽으면 되고, outcome은 같은
        주기 안에서 candidate 뒤에 붙으므로 대기 중인 candidate만 창으로 들고
        있으면 된다. 파일 전체를 dict 두 개에 담으면 보존 기간이 찬 시점에
        1GB 인스턴스가 감당할 수 없는 크기가 된다.
        """
        if self._compact_observations_if_needed():
            self._reset_sample_state()
        if not self._observation_file.exists():
            self._reset_sample_state()
            return self._training_samples
        with self._file_lock:
            size = self._observation_file.stat().st_size
            if size < self._samples_offset:
                # 압축·교체로 파일이 줄었으면 처음부터 다시 만든다.
                self._reset_sample_state()
            elif size == self._samples_offset:
                return self._training_samples
            with self._observation_file.open("rb") as handle:
                handle.seek(self._samples_offset)
                for raw in handle:
                    if not raw.endswith(b"\n"):
                        # 아직 쓰는 중인 마지막 줄은 다음 호출로 미룬다.
                        break
                    self._samples_offset += len(raw)
                    try:
                        item = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if isinstance(item, dict):
                        self._consume_observation(item)
        return self._training_samples
