"""하루치 헤드라인을 한 번에 분석해 시장 감성과 분류 건수를 산출한다."""

import json
import logging
import math
from pathlib import Path
from typing import Any

from services.telegram_bot.llm.backends import LLMBackend

logger = logging.getLogger(__name__)


class MarketDigestError(RuntimeError):
    """Raised when a daily market digest cannot be produced."""


class MarketDigestAnalyzer:
    def __init__(
        self,
        backend: LLMBackend,
        prompt_file: Path,
        num_predict: int = 256,
        count_tolerance_ratio: float = 0.2,
    ):
        self._backend = backend
        self._num_predict = num_predict
        self._count_tolerance_ratio = max(0.0, float(count_tolerance_ratio))
        self._prompt = prompt_file.read_text(encoding="utf-8")

    def _count_tolerance(self, expected_count: int) -> int:
        """건수를 믿을지 정하는 허용 오차. 최소 1건은 봐준다.

        목록이 길수록 세기가 나빠지므로 비율로 잡는다. 넘겨도 그날을 버리지는
        않는다(`_parse` 참고) — 이 값은 "건수를 저장할 만한가"의 기준이지
        그날의 감성이 쓸 만한가의 기준이 아니다.
        """
        return max(1, math.ceil(expected_count * self._count_tolerance_ratio))

    def analyze(
        self,
        market: str,
        day: str,
        headlines: list[str],
    ) -> dict[str, Any]:
        """헤드라인 목록에서 그날의 종합 감성을 만든다(블로킹)."""
        if not headlines:
            raise MarketDigestError("no headlines to summarize")

        payload = {"market": market, "date": day, "headlines": headlines}
        try:
            raw = self._backend.generate(
                system_prompt=self._prompt,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                max_tokens=self._num_predict,
                temperature=0.2,
            )
        except Exception as exc:
            raise MarketDigestError(str(exc)) from exc

        if not raw.strip():
            raise MarketDigestError("empty digest response content")
        return self._parse(raw, expected_count=len(headlines))

    def _parse(self, raw: str, *, expected_count: int) -> dict[str, Any]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            # 원문은 남기지 않는다. 길이만으로도 잘림 여부는 판단할 수 있다.
            raise MarketDigestError(
                f"digest JSON parse failed ({exc}); raw_chars={len(raw)}"
            ) from exc
        if not isinstance(data, dict):
            raise MarketDigestError("digest JSON must be an object")

        sentiment = data.get("sentiment")
        if not isinstance(sentiment, (int, float)):
            raise MarketDigestError("digest sentiment must be a number")

        raw_counts = {
            name: data.get(name)
            for name in ("positive", "negative", "neutral")
        }
        if not all(isinstance(value, int) for value in raw_counts.values()):
            # 필드가 비었으면 envelope이 깨진 응답이다. 여기까지는 엄격히 본다.
            raise MarketDigestError("digest counts are missing")
        counts = {name: int(value) for name, value in raw_counts.items()}
        normalized_counts: dict[str, int | None] = dict(counts)

        total = sum(counts.values())
        drift = abs(total - expected_count)
        tolerance = self._count_tolerance(expected_count)
        if drift > tolerance:
            # 건수만 버리고 그날은 살린다. 차트가 읽는 값은 sentiment와 summary뿐이고
            # 건수는 저장만 될 뿐 읽는 곳이 없다. 반대로 그날을 통째로 버리면 캐시에
            # 아무것도 남지 않아 `missing_digest_days`가 매번 다시 집어오고,
            # `/market`을 부를 때마다 같은 날을 다시 받아 다시 호출하게 된다.
            logger.warning(
                "[DIGEST] 건수를 믿을 수 없어 버림 (counts=%d headlines=%d, 허용 %d)",
                total,
                expected_count,
                tolerance,
            )
            normalized_counts = dict.fromkeys(normalized_counts, None)
        elif drift:
            logger.info(
                "[DIGEST] 건수 합 오차 %d (counts=%d headlines=%d, 허용 %d)",
                drift,
                total,
                expected_count,
                tolerance,
            )

        return {
            "sentiment": max(-1.0, min(1.0, float(sentiment))),
            "summary": str(data.get("summary") or "").strip(),
            **normalized_counts,
        }
