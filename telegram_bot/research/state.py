"""리서치 상태(`history`·`sight`·`updated_at`·`last_result`) 저장소.

`llm/`에서 옮겨왔다(2026-08-30) — `llm/`은 LLM 백엔드 호출 분석기만 담는
디렉터리인데, 상태 영속화를 갖는 클래스가 이 파일 하나뿐이었다. 분석기
(`MarketViewAnalyzer`)는 여전히 `llm/market_view.py`에 있다.
"""

import json
import logging
from pathlib import Path
from typing import Any

from shared.core.clock import now
from shared.core.storage import write_json_atomic

logger = logging.getLogger(__name__)

SIGHT_KEY = "sight"


class MarketViewManager:
    def __init__(self, file_path: Path, history_limit: int = 5):
        self._file_path = file_path
        self._history_limit = max(1, history_limit)
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self._file_path.exists():
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            self._data = self._default_data()
            self._persist()
            return

        try:
            self._data = json.loads(self._file_path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                raise ValueError("market view state must be an object")
        except Exception as e:
            logger.warning("[MarketView] failed to load state, resetting: %s", e)
            self._data = self._default_data()
            self._persist()

    def _default_data(self) -> dict[str, Any]:
        return {SIGHT_KEY: None, "updated_at": None, "history": [], "last_result": None}

    def _persist(self) -> None:
        write_json_atomic(self._file_path, self._data, indent=2)

    def get_sight(self) -> str | None:
        sight = self._data.get(SIGHT_KEY)
        return sight if isinstance(sight, str) and sight.strip() else None

    def set_sight(self, text: str) -> None:
        normalized = text.strip()
        if normalized != self.get_sight():
            # 주제가 바뀌면 이전 분석은 맥락이 다르다. 프롬프트에 넣지 않는 것과
            # 같은 이유로 `/research show`에도 남기지 않는다.
            self._data["history"] = []
            self._data["last_result"] = None
        self._data[SIGHT_KEY] = normalized
        self._data["updated_at"] = now().isoformat(timespec="seconds")
        self._persist()

    def clear_sight(self) -> None:
        self._data[SIGHT_KEY] = None
        self._data["updated_at"] = None
        self._data["history"] = []
        self._data["last_result"] = None
        self._persist()

    def get_last_result(self) -> dict[str, Any] | None:
        """마지막 분석의 **전체** 결과. `/research show`가 그대로 다시 그린다."""
        last = self._data.get("last_result")
        return last if isinstance(last, dict) else None

    def save_result(
        self,
        result: dict[str, Any],
        *,
        news_count: int = 0,
        candidate_count: int = 0,
    ) -> None:
        """압축본은 history에, 전체는 last_result에 따로 적는다.

        둘을 합치지 않는 이유는 쓰임이 달라서다. history는 다음 분석 프롬프트에
        맥락으로 들어가므로 짧아야 하고(입력 토큰이 부풀면 응답이 잘린다),
        `/research show`는 근거·리스크·반론까지 다시 보여 줘야 한다. 하나로
        묶으면 프롬프트가 비대해지거나 show가 얇아진다.

        전체는 **마지막 한 건만** 남긴다. 이력을 통째로 쌓을 이유가 없다.
        """
        history = self._data.get("history")
        if not isinstance(history, list):
            history = []
        history.append(self._summarize_result(result))
        self._data["history"] = history[-self._history_limit :]
        # 입력 규모는 결과에 없으므로 저장 시점에 함께 적는다. show가 헤더에
        # "분석 뉴스 N건 / 후보 M개"를 실행 때와 똑같이 그리기 위해서다.
        self._data["last_result"] = {
            **result,
            "news_count": news_count,
            "candidate_count": candidate_count,
        }
        self._persist()

    def get_history_summaries(self) -> list[dict[str, Any]]:
        """이전 분석의 압축 요약. 다음 분석 프롬프트에 맥락으로 주입한다."""
        history = self._data.get("history")
        if not isinstance(history, list):
            return []
        return history[-self._history_limit :]

    @staticmethod
    def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
        actions = [
            {
                "ticker": str(item.get("ticker") or "").strip(),
                "action": item.get("action"),
            }
            for item in result.get("actions", [])
            if (
                isinstance(item, dict)
                and item.get("action") in ("add", "remove")
                and str(item.get("ticker") or "").strip()
            )
        ]
        return {
            "generated_at": result.get("generated_at"),
            "summary": str(result.get("summary") or "")[:300],
            "actions": actions,
        }
