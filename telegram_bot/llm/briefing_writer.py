"""브리핑 코멘트 작성기.

요약 스냅샷·뉴스 헤드라인·시장뷰를 받아 짧은 한국어 코멘트를 생성한다.
실패하면 호출부가 데이터 전용 브리핑으로 대체하도록 예외를 던진다.
실제 호출은 주입받은 Cloudflare 백엔드가 담당한다.
"""

import json
import logging
from pathlib import Path
from typing import Any

from telegram_bot.llm.backends import LLMBackend

logger = logging.getLogger(__name__)


class BriefingError(RuntimeError):
    """Raised when briefing comment generation fails."""


class BriefingWriter:
    def __init__(
        self,
        backend: LLMBackend,
        enabled: bool,
        prompt_file: Path,
        num_predict: int = 512,
    ):
        self._backend = backend
        self._enabled = enabled
        self._num_predict = num_predict
        self._prompt = prompt_file.read_text(encoding="utf-8")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def write(self, payload: dict[str, Any]) -> str:
        """payload를 근거로 3~5문장 코멘트를 생성한다(블로킹)."""
        if not self._enabled:
            raise BriefingError("briefing LLM is disabled")
        try:
            content = self._backend.generate(
                system_prompt=self._prompt,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                max_tokens=self._num_predict,
                temperature=0.3,
            )
            if not content.strip():
                raise BriefingError("empty briefing response content")
            return content.strip()
        except BriefingError:
            raise
        except Exception as e:
            raise BriefingError(str(e)) from e
