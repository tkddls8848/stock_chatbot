"""원문 뉴스 사건 메모리, 후보 점수화, 관측/CPU 예산 관리."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import math
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from shared.core.clock import now
from shared.core.storage import write_json_atomic
from telegram_bot.features.news_prefilter.cpu_budget import DailyCpuBudget
from telegram_bot.features.news_prefilter.learning import (
    PENDING_CANDIDATE_LIMIT as _PENDING_CANDIDATE_LIMIT,
    ObservationLearner,
)
from telegram_bot.features.news_prefilter.matcher import StockEntityMatcher
from telegram_bot.features.news_prefilter.optimizer import (
    OptimizationResult,
    optimize_for_cpu_budget,
    predict_probability,
)
from telegram_bot.news.sources import GlobalArticle

logger = logging.getLogger(__name__)
_SPACE_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")
# 섀도 비교가 답하지 못하는 것. 지운 채로 active에 올리지 않는다.
SHADOW_CAVEATS = (
    "라벨은 보고서 근거와 무작위 평가 표본에 붙는다. shadow의 입력은 최신순 상위뿐이라"
    " 사전선별이 새로 끌어올린 기사의 impact는 끝내 관측되지 않는다.",
    "따라서 판별력 수치는 '최신순이 이미 고른 기사들 안에서의 순위'이지"
    " '더 나은 기사를 찾아내는 능력'이 아니다."
    " 후자는 active의 탐색 슬롯으로만 측정된다.",
)
_HEADLINE_TERMS = (
    "earnings",
    "guidance",
    "acquisition",
    "merger",
    "bankruptcy",
    "default",
    "investigation",
    "sanction",
    "rate cut",
    "rate hike",
    "실적",
    "인수",
    "합병",
    "파산",
    "부도",
    "조사",
    "제재",
    "금리 인하",
    "금리 인상",
    "业绩",
    "收购",
    "合并",
    "破产",
    "违约",
    "调查",
    "制裁",
    "降息",
    "加息",
)


@dataclass
class EventRecord:
    event_id: str
    text: str
    signature: int
    first_seen: str
    last_seen: str
    sources: list[str] = field(default_factory=list)
    article_ids: list[str] = field(default_factory=list)
    occurrences: int = 0
    # 이 사건으로 실제 번역이 나간 마지막 시각. 재탕 차단이 읽는다.
    translated_at: str = ""


@dataclass(frozen=True)
class RankedCandidate:
    article: GlobalArticle
    candidate_id: str
    event_id: str
    score: float
    features: dict[str, float]
    prefilter_rank: int
    exploration: bool = False


def _normalize_text(article: GlobalArticle) -> str:
    raw = f"{article.title}\n{article.content[:360]}"
    stripped = _TAG_RE.sub(" ", html.unescape(raw)).lower()
    return _SPACE_RE.sub(" ", stripped).strip()[:600]


@lru_cache(maxsize=2048)
def _char_ngrams(text: str) -> Counter[str]:
    padded = f" {text} "
    return Counter(padded[index : index + 3] for index in range(max(0, len(padded) - 2)))


def _simhash(text: str) -> int:
    vector = [0] * 64
    for gram, count in _char_ngrams(text).items():
        hashed = int.from_bytes(
            hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(),
            "big",
        )
        for bit in range(64):
            vector[bit] += count if hashed & (1 << bit) else -count
    signature = 0
    for bit, value in enumerate(vector):
        if value >= 0:
            signature |= 1 << bit
    return signature


def _cosine_similarity(left: str, right: str) -> float:
    left_counts = _char_ngrams(left)
    right_counts = _char_ngrams(right)
    if not left_counts or not right_counts:
        return 0.0
    shared = left_counts.keys() & right_counts.keys()
    numerator = sum(left_counts[key] * right_counts[key] for key in shared)
    left_norm = math.sqrt(sum(value * value for value in left_counts.values()))
    right_norm = math.sqrt(sum(value * value for value in right_counts.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _bands(signature: int) -> tuple[tuple[int, int], ...]:
    return tuple((index, (signature >> (index * 16)) & 0xFFFF) for index in range(4))


def _rank_auc(scored: list[tuple[float, int]]) -> float | None:
    """점수가 impact 라벨을 얼마나 갈라내는지(0.5 = 무작위와 같음).

    동점은 절반씩 나눠 세는 표준 rank 정의를 쓴다 — 모델이 없어 모든 점수가
    같을 때 1.0이 아니라 0.5가 나와야 한다.
    """
    positives = sum(label for _, label in scored)
    negatives = len(scored) - positives
    if not positives or not negatives:
        return None
    ordered = sorted(scored, key=lambda row: row[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = (index + end + 1) / 2
        rank_sum += sum(average_rank for _, label in ordered[index:end] if label)
        index = end
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


class NewsPrefilter:
    def __init__(
        self,
        *,
        stock_db,
        event_file: Path,
        observation_file: Path,
        model_file: Path,
        cpu_state_file: Path,
        mode: str,
        event_window_hours: int,
        max_events: int,
        observation_retention_days: int,
        similarity_threshold: float,
        exploration_slots: int,
        translate_limit: int,
        translated_event_cooldown_hours: int,
        daily_cpu_budget_seconds: float,
    ):
        self.mode = mode
        self._event_file = event_file
        self._observation_file = observation_file
        self._model_file = model_file
        self._event_window = timedelta(hours=max(1, event_window_hours))
        self._max_events = max(100, max_events)
        self._observation_retention_days = max(1, observation_retention_days)
        self._similarity_threshold = min(0.99, max(0.1, similarity_threshold))
        self._exploration_slots = max(0, min(exploration_slots, translate_limit - 1))
        self._translate_limit = max(1, translate_limit)
        self._translated_cooldown = timedelta(
            hours=max(0, translated_event_cooldown_hours)
        )
        self._daily_cpu_budget_seconds = max(0.0, daily_cpu_budget_seconds)
        self._lock = asyncio.Lock()
        self._file_lock = threading.RLock()
        self._optimizer = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="news-prefilter-cpu",
        )
        self._events = self._load_events()
        raw_model = self._load_json(self._model_file, default={})
        self._model = raw_model if isinstance(raw_model, dict) else {}
        self._cpu_budget = DailyCpuBudget(
            cpu_state_file,
            self._daily_cpu_budget_seconds,
        )
        self._last_persist_monotonic = 0.0
        self._learner = ObservationLearner(
            observation_file,
            self._observation_retention_days,
            self._file_lock,
        )
        # 재탕 차단용. record_outcome은 candidate_id만 받으므로 어느 사건의
        # 후보였는지 여기서 기억한다.
        self._candidate_events: dict[str, str] = {}
        self._cycle_id = ""
        self._cycle_claimed: set[str] = set()
        self._matcher = StockEntityMatcher(stock_db.get_candidate_universe())
        logger.info(
            "[PREFILTER] %s 모드 · 사건 %d건 · 종목명 패턴 %d개 · CPU 예산 %.2fh/일",
            self.mode,
            len(self._events),
            self._matcher.pattern_count,
            self._daily_cpu_budget_seconds / 3600,
        )

    @staticmethod
    def _load_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return default

    def _load_events(self) -> dict[str, EventRecord]:
        raw = self._load_json(self._event_file, default=[])
        if not isinstance(raw, list):
            return {}
        events: dict[str, EventRecord] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                event = EventRecord(
                    event_id=str(item["event_id"]),
                    text=str(item["text"]),
                    signature=int(item["signature"]),
                    first_seen=str(item["first_seen"]),
                    last_seen=str(item["last_seen"]),
                    sources=[str(value) for value in item.get("sources") or []],
                    article_ids=[str(value) for value in item.get("article_ids") or []],
                    occurrences=int(item.get("occurrences") or 0),
                    translated_at=str(item.get("translated_at") or ""),
                )
            except (KeyError, TypeError, ValueError):
                continue
            events[event.event_id] = event
        return events

    def account_foreground_cpu(self) -> float:
        return self._cpu_budget.account_foreground_cpu()

    def record_background_cpu(self, cpu_seconds: float) -> None:
        self._cpu_budget.record_background_cpu(cpu_seconds)

    @property
    def remaining_cpu_seconds(self) -> float:
        return self._cpu_budget.remaining_seconds

    def cpu_status(self) -> dict[str, float | str]:
        return self._cpu_budget.status()

    @staticmethod
    def _candidate_id(source: str, article_id: str) -> str:
        value = f"{source}\0{article_id}".encode("utf-8", errors="replace")
        return hashlib.sha1(value).hexdigest()

    def _event_indexes(self):
        buckets: dict[tuple[int, int], set[str]] = defaultdict(set)
        exact: dict[str, str] = {}
        for event in self._events.values():
            exact[event.text] = event.event_id
            for band in _bands(event.signature):
                buckets[band].add(event.event_id)
        return buckets, exact

    def _evict_events(self, observed_at: datetime) -> None:
        cutoff = observed_at - self._event_window
        kept: list[EventRecord] = []
        for event in self._events.values():
            try:
                last_seen = datetime.fromisoformat(event.last_seen)
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=observed_at.tzinfo)
            except ValueError:
                continue
            if last_seen >= cutoff:
                kept.append(event)
        kept.sort(key=lambda event: event.last_seen, reverse=True)
        self._events = {event.event_id: event for event in kept[: self._max_events]}

    def _match_event(
        self,
        text: str,
        signature: int,
        buckets: dict[tuple[int, int], set[str]],
        exact: dict[str, str],
    ) -> tuple[EventRecord | None, float]:
        exact_id = exact.get(text)
        if exact_id:
            return self._events.get(exact_id), 1.0
        candidate_ids: set[str] = set()
        for band in _bands(signature):
            candidate_ids.update(buckets.get(band, ()))
        best_event = None
        best_similarity = 0.0
        for event_id in candidate_ids:
            event = self._events.get(event_id)
            if event is None:
                continue
            similarity = _cosine_similarity(text, event.text)
            if similarity > best_similarity:
                best_event = event
                best_similarity = similarity
        if best_similarity < self._similarity_threshold:
            return None, best_similarity
        return best_event, best_similarity

    def _recently_translated(self, event_id: str, observed_at: datetime) -> bool:
        """이 사건으로 이미 번역이 나갔고 아직 재탕 차단 시간 안인가."""
        if not self._translated_cooldown:
            return False
        event = self._events.get(event_id)
        if event is None or not event.translated_at:
            return False
        try:
            translated = datetime.fromisoformat(event.translated_at)
        except ValueError:
            return False
        if translated.tzinfo is None:
            translated = translated.replace(tzinfo=observed_at.tzinfo)
        return observed_at - translated < self._translated_cooldown

    def _mark_event_translated(self, candidate_id: str) -> None:
        event_id = self._candidate_events.get(candidate_id)
        event = self._events.get(event_id) if event_id else None
        if event is None:
            return
        event.translated_at = now().isoformat(timespec="seconds")
        self._persist_events_if_due()

    def _headline_signal(self, title: str) -> float:
        lowered = title.lower()
        return 1.0 if any(term in lowered for term in _HEADLINE_TERMS) else 0.0

    def _append_observations(self, payloads: list[dict[str, Any]]) -> None:
        if not payloads:
            return
        body = "".join(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            for payload in payloads
        )
        with self._file_lock:
            self._observation_file.parent.mkdir(parents=True, exist_ok=True)
            with self._observation_file.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(body)

    def _persist_events_if_due(self) -> None:
        current = time.monotonic()
        if current - self._last_persist_monotonic < 60:
            return
        payload = [asdict(event) for event in self._events.values()]
        write_json_atomic(self._event_file, payload)
        self._last_persist_monotonic = current

    def _rank_sync(
        self,
        *,
        source: str,
        market: str,
        articles: list[GlobalArticle],
        watchlist: dict[str, str],
        cycle_id: str,
    ) -> list[RankedCandidate]:
        observed_at = now()
        self._evict_events(observed_at)
        if cycle_id != self._cycle_id:
            self._cycle_id = cycle_id
            self._cycle_claimed = set()
        buckets, exact = self._event_indexes()
        rows: list[RankedCandidate] = []
        scored_rows: list[tuple[GlobalArticle, str, str, dict[str, float], float]] = []
        count = max(1, len(articles) - 1)

        for feed_rank, article in enumerate(articles):
            candidate_id = self._candidate_id(source, article.article_id)
            text = _normalize_text(article)
            signature = _simhash(text)
            event, similarity = self._match_event(text, signature, buckets, exact)
            is_new_event = event is None
            if event is None:
                event_id = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
                event = EventRecord(
                    event_id=event_id,
                    text=text,
                    signature=signature,
                    first_seen=observed_at.isoformat(timespec="seconds"),
                    last_seen=observed_at.isoformat(timespec="seconds"),
                )
                self._events[event_id] = event
                exact[text] = event_id
                for band in _bands(signature):
                    buckets[band].add(event_id)
            already_seen = candidate_id in event.article_ids
            if not already_seen:
                event.article_ids.append(candidate_id)
                event.article_ids = event.article_ids[-32:]
                event.occurrences += 1
            if source not in event.sources:
                event.sources.append(source)
            event.last_seen = observed_at.isoformat(timespec="seconds")

            matches = self._matcher.match(f"{article.title}\n{article.content}")
            watchlist_hit = bool(matches.codes & set(watchlist))
            source_diversity = min(1.0, len(event.sources) / 3.0)
            features = {
                "freshness": max(0.0, 1.0 - feed_rank / count),
                "watchlist_hit": float(watchlist_hit),
                "entity_density": min(1.0, len(matches.codes) / 3.0),
                "novelty": 1.0 if is_new_event else max(0.0, 1.0 - similarity),
                "source_diversity": source_diversity,
                "headline_signal": self._headline_signal(article.title),
                "duplicate_similarity": similarity,
            }
            heuristic = (
                1.4 * features["freshness"]
                + 3.5 * features["watchlist_hit"]
                + 0.8 * features["entity_density"]
                + 2.0 * features["novelty"]
                + 1.2 * features["source_diversity"]
                + 1.4 * features["headline_signal"]
                - 2.2 * features["duplicate_similarity"]
                - (1.0 if already_seen else 0.0)
            )
            impact_probability = predict_probability(self._model, article.title, features)
            score = heuristic + 3.0 * (impact_probability - 0.5)
            scored_rows.append((article, candidate_id, event.event_id, features, score))
            self._candidate_events[candidate_id] = event.event_id

        # ── 번역 대상에서 아예 빼는 후보 ────────────────
        # 점수 순서를 바꾸는 일(shadow/active의 쟁점)과 달리, 여기서 거르는 것은
        # "같은 사건을 다시 번역하는 것"뿐이다. 두 정책 모두 걸러진 뒤의 같은
        # 풀에서 고르므로 섀도 비교의 baseline은 그대로 유지된다.
        gate_counts = {"translated": 0, "cycle": 0, "source": 0}
        raw_rows: list[tuple[GlobalArticle, str, str, dict[str, float], float]] = []
        source_events: set[str] = set()
        for row in scored_rows:
            event_id = row[2]
            if self._recently_translated(event_id, observed_at):
                gate_counts["translated"] += 1
                continue
            if event_id in self._cycle_claimed:
                gate_counts["cycle"] += 1
                continue
            if event_id in source_events:
                gate_counts["source"] += 1
                continue
            source_events.add(event_id)
            raw_rows.append(row)
        while len(self._candidate_events) > _PENDING_CANDIDATE_LIMIT:
            self._candidate_events.pop(next(iter(self._candidate_events)))

        ranked_indexes = sorted(
            range(len(raw_rows)),
            key=lambda index: (raw_rows[index][4], -index),
            reverse=True,
        )
        exploration_indexes: set[int] = set()
        if self._exploration_slots and len(ranked_indexes) > self._translate_limit:
            fixed_count = max(1, self._translate_limit - self._exploration_slots)
            pool = ranked_indexes[fixed_count:]
            seed = int.from_bytes(
                hashlib.blake2b(
                    f"{cycle_id}:{source}".encode("utf-8"), digest_size=8
                ).digest(),
                "big",
            )
            for offset in range(min(self._exploration_slots, len(pool))):
                chosen = pool[(seed + offset * 104729) % len(pool)]
                exploration_indexes.add(chosen)
            selected = ranked_indexes[:fixed_count] + list(exploration_indexes)
            ranked_indexes = selected + [
                index for index in ranked_indexes if index not in set(selected)
            ]

        prefilter_rank = {index: rank for rank, index in enumerate(ranked_indexes)}
        observation_lines: list[dict[str, Any]] = []
        new_events = 0
        for index, (article, candidate_id, event_id, features, score) in enumerate(raw_rows):
            latest_selected = index < self._translate_limit
            prefilter_selected = prefilter_rank[index] < self._translate_limit
            exploration = index in exploration_indexes
            rows.append(
                RankedCandidate(
                    article=article,
                    candidate_id=candidate_id,
                    event_id=event_id,
                    score=score,
                    features=features,
                    prefilter_rank=prefilter_rank[index],
                    exploration=exploration,
                )
            )
            if features["novelty"] >= 1.0:
                new_events += 1
            # 두 정책이 실제로 고르는 기사와 탐색분만 남긴다. 라벨은 번역된
            # 기사에만 붙으므로 나머지 수백 건을 적어도 학습에 쓸 수 없고,
            # 하루 10만 줄이 쌓여 보존 기간 안에 디스크와 압축 비용만 키운다.
            if not (latest_selected or prefilter_selected or exploration):
                continue
            observation_lines.append(
                {
                    "type": "candidate",
                    "observed_at": observed_at.isoformat(timespec="seconds"),
                    "cycle_id": cycle_id,
                    "candidate_id": candidate_id,
                    "source": source,
                    "market": str(article.extra.get("market") or market or "OTHER"),
                    "feed_rank": index,
                    "title": article.title[:240],
                    "published_at": article.published_at,
                    "event_id": event_id,
                    "features": features,
                    "score": round(score, 6),
                    "prefilter_rank": prefilter_rank[index],
                    "latest_selected": latest_selected,
                    "prefilter_selected": prefilter_selected,
                    "exploration": exploration,
                    "mode": self.mode,
                }
            )
        # 개별 후보를 남기지 않는 대신 주기별 집계로 사건·중복 추이를 남긴다.
        observation_lines.append(
            {
                "type": "cycle",
                "observed_at": observed_at.isoformat(timespec="seconds"),
                "cycle_id": cycle_id,
                "source": source,
                "candidates": len(raw_rows),
                "gated_translated_event": gate_counts["translated"],
                "gated_cycle_duplicate": gate_counts["cycle"],
                "gated_source_duplicate": gate_counts["source"],
                "new_events": new_events,
                "logged": len(observation_lines),
                "mode": self.mode,
            }
        )
        self._append_observations(observation_lines)

        ordered = sorted(rows, key=lambda row: row.prefilter_rank)
        selected = rows if self.mode == "shadow" else ordered
        # 이번 주기에 번역될 사건을 찍어 둔다. 뒤에 도는 소스가 같은 사건을
        # 다시 번역하지 않는다 — 소스 여섯 곳이 같은 발표를 옮겨 적는 것이
        # 한 주기 안에서 가장 흔한 중복이다.
        self._cycle_claimed.update(
            row.event_id for row in selected[: self._translate_limit]
        )
        self._persist_events_if_due()
        if any(gate_counts.values()):
            logger.info(
                "[PREFILTER] %s 재탕 차단 %d건(기번역 %d · 주기중복 %d · 소스중복 %d)",
                source,
                sum(gate_counts.values()),
                gate_counts["translated"],
                gate_counts["cycle"],
                gate_counts["source"],
            )
        return selected

    async def rank_articles(
        self,
        *,
        source: str,
        market: str,
        articles: list[GlobalArticle],
        watchlist: dict[str, str],
        cycle_id: str,
    ) -> list[RankedCandidate]:
        async with self._lock:
            return await asyncio.to_thread(
                self._rank_sync,
                source=source,
                market=market,
                articles=articles,
                watchlist=watchlist,
                cycle_id=cycle_id,
            )

    async def record_outcome(
        self,
        *,
        candidate_id: str,
        impact: str,
        sentiment: float | None,
        selected: bool = True,
    ) -> None:
        if not candidate_id:
            return
        payload = {
            "type": "outcome",
            "observed_at": now().isoformat(timespec="seconds"),
            "candidate_id": candidate_id,
            "impact": str(impact or ""),
            "sentiment": sentiment,
        }
        async with self._lock:
            await asyncio.to_thread(self._record_outcome_sync, payload, candidate_id, selected)

    def _record_outcome_sync(self, payload: dict[str, Any], candidate_id: str, selected: bool = True) -> None:
        self._append_observations([payload])
        if selected:
            self._mark_event_translated(candidate_id)

    def _report_sync(self) -> dict[str, Any]:
        """관측 파일을 한 번 훑어 섀도 비교 지표를 만든다."""
        cycles = 0
        candidates_seen = 0
        logged = 0
        new_events = 0
        both = 0
        latest_only = 0
        prefilter_only = 0
        scored: list[tuple[float, int]] = []
        pending: dict[str, tuple[float, bool, bool]] = {}
        if self._observation_file.exists():
            with self._file_lock, self._observation_file.open("rb") as handle:
                for raw in handle:
                    try:
                        item = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not isinstance(item, dict):
                        continue
                    kind = item.get("type")
                    if kind == "cycle":
                        cycles += 1
                        candidates_seen += int(item.get("candidates") or 0)
                        logged += int(item.get("logged") or 0)
                        new_events += int(item.get("new_events") or 0)
                        continue
                    candidate_id = str(item.get("candidate_id") or "")
                    if not candidate_id:
                        continue
                    if kind == "candidate":
                        latest = bool(item.get("latest_selected"))
                        chosen = bool(item.get("prefilter_selected"))
                        if latest and chosen:
                            both += 1
                        elif latest:
                            latest_only += 1
                        elif chosen:
                            prefilter_only += 1
                        pending[candidate_id] = (
                            float(item.get("score") or 0.0),
                            latest,
                            chosen,
                        )
                        while len(pending) > _PENDING_CANDIDATE_LIMIT:
                            pending.pop(next(iter(pending)))
                    elif kind == "outcome":
                        found = pending.pop(candidate_id, None)
                        impact = str(item.get("impact") or "")
                        if found is None or impact not in {"high", "medium", "low"}:
                            continue
                        scored.append((found[0], int(impact in {"high", "medium"})))

        model = self._model if isinstance(self._model, dict) else {}
        return {
            "mode": self.mode,
            "cycles": cycles,
            "candidates_seen": candidates_seen,
            "logged": logged,
            "new_event_ratio": (new_events / candidates_seen) if candidates_seen else None,
            "events": len(self._events),
            "agree": both,
            "latest_only": latest_only,
            "prefilter_only": prefilter_only,
            "labeled": len(scored),
            "positives": sum(label for _, label in scored),
            "auc": _rank_auc(scored),
            "model_trained_at": str(model.get("trained_at") or ""),
            "model_validation_ap": model.get("validation_ap"),
            "model_label_count": model.get("label_count"),
            "model_prevalence": model.get("validation_prevalence"),
            "cpu": self.cpu_status(),
        }

    async def report(self) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._report_sync)

    def _optimize_sync(self, cpu_seconds: float) -> OptimizationResult:
        return optimize_for_cpu_budget(
            self._learner.load_training_samples(),
            dict(self._model),
            cpu_seconds,
        )

    async def optimize_chunk(self, cpu_seconds: float) -> OptimizationResult:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self._optimizer,
            self._optimize_sync,
            max(0.05, cpu_seconds),
        )
        if result.model is not None:
            model = dict(result.model)
            model["trained_at"] = now().isoformat(timespec="seconds")
            async with self._lock:
                self._model = model
                await asyncio.to_thread(write_json_atomic, self._model_file, model)
        return result
