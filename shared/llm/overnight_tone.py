"""전일 마감 뒤~당일 개장 전 헤드라인의 시장 논조를 한 번에 채점한다."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from shared.llm.backends import LLMBackend


class OvernightToneError(RuntimeError):
    """Raised when the overnight tone response is unusable."""


class OvernightToneAnalyzer:
    def __init__(
        self,
        backend: LLMBackend,
        prompt_file: Path,
        *,
        model_id: str,
        num_predict: int = 384,
    ):
        self._backend = backend
        self._prompt = prompt_file.read_text(encoding="utf-8")
        self._num_predict = num_predict
        self.model_id = model_id
        self.prompt_sha256 = hashlib.sha256(self._prompt.encode("utf-8")).hexdigest()

    def analyze(
        self,
        market: str,
        price_session: str,
        sentiment_for_session: str,
        window_start: str,
        window_end: str,
        headlines: list[dict[str, str]],
    ) -> dict[str, Any]:
        if not headlines:
            raise OvernightToneError("no headlines to analyze")
        payload = {
            "market": market,
            "price_session": price_session,
            "sentiment_for_session": sentiment_for_session,
            "window": {"start": window_start, "end": window_end},
            # 전일 등락률은 의도적으로 넣지 않는다. 모델이 가격 방향에 맞춰
            # 논조를 보정하면 측정하려는 일치·불일치가 사라진다.
            "headlines": headlines,
        }
        try:
            raw = self._backend.generate(
                system_prompt=self._prompt,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                max_tokens=self._num_predict,
                temperature=0.0,
            )
        except Exception as exc:
            raise OvernightToneError(str(exc)) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OvernightToneError(
                f"tone JSON parse failed ({exc}); raw_chars={len(raw)}"
            ) from exc
        if not isinstance(data, dict):
            raise OvernightToneError("tone response must be an object")
        result = {}
        for field in ("tone", "forward"):
            value = data.get(field)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise OvernightToneError(f"{field} must be a finite number")
            result[field] = max(-1.0, min(1.0, float(value)))
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise OvernightToneError("summary must be a non-empty string")
        result["summary"] = summary.strip()
        result["model_id"] = self.model_id
        result["prompt_sha256"] = self.prompt_sha256
        return result
