"""하루치 헤드라인을 한 번에 분석해 헤드라인별 점수와 그 평균(그날의 시장 감성)을 산출한다."""

import json
import logging
import math
from pathlib import Path
from typing import Any

from services.telegram_bot.llm.backends import LLMBackend
from services.telegram_bot.llm.terminology import read_prompt

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
        self._prompt = read_prompt(prompt_file)

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
        """그날 값은 헤드라인별 점수의 평균이다(2026-09-30 운영자 결정).

        예전에는 모델이 그날 분위기를 숫자 하나로 매겼다. 값이 ±0.15·0.25·0.45처럼
        띄엄띄엄 나오고 하루하루 크게 흔들려(표준편차 0.26~0.39) 30일 추세가 0 근처로
        뭉개졌다. 같은 호출에서 헤드라인마다 점수를 받아 평균하면 호출 수는 그대로다.
        점수 목록이 깨진 날은 모델의 종합 판단(`sentiment`)으로 살린다 — 그날을 버리면
        캐시가 비어 예약 갱신마다 같은 날을 다시 부른다.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            # 원문은 남기지 않는다. 길이만으로도 잘림 여부는 판단할 수 있다.
            raise MarketDigestError(
                f"digest JSON parse failed ({exc}); raw_chars={len(raw)}"
            ) from exc
        if not isinstance(data, dict):
            raise MarketDigestError("digest JSON must be an object")

        overall = data.get("sentiment")
        if not isinstance(overall, (int, float)) or isinstance(overall, bool):
            raise MarketDigestError("digest sentiment must be a number")
        overall = max(-1.0, min(1.0, float(overall)))

        raw_scores = data.get("scores")
        scores: list[float] = []
        if isinstance(raw_scores, list):
            scores = [
                max(-1.0, min(1.0, float(value)))
                for value in raw_scores[:expected_count]
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            ]
        tolerance = self._count_tolerance(expected_count)
        if scores and expected_count - len(scores) <= tolerance:
            if len(scores) != expected_count:
                logger.info(
                    "[DIGEST] 점수 개수 오차 %d (scores=%d headlines=%d, 허용 %d)",
                    expected_count - len(scores), len(scores), expected_count, tolerance,
                )
            sentiment = sum(scores) / len(scores)
            counts: dict[str, int | None] = {
                "positive": sum(1 for value in scores if value > 0.1),
                "negative": sum(1 for value in scores if value < -0.1),
                "neutral": sum(1 for value in scores if -0.1 <= value <= 0.1),
            }
        else:
            logger.warning(
                "[DIGEST] 헤드라인 점수를 쓸 수 없어 종합 판단으로 대체 (scores=%d headlines=%d, 허용 %d)",
                len(scores), expected_count, tolerance,
            )
            sentiment, scores = overall, []
            counts = dict.fromkeys(("positive", "negative", "neutral"))

        return {
            "sentiment": round(sentiment, 4),
            "overall": overall,
            "scored": len(scores),
            "summary": str(data.get("summary") or "").strip(),
            **counts,
        }
