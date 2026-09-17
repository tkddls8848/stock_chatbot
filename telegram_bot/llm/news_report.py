"""3시간 동안 모은 기사 제목을 시장상황 보고서로 추론한다.

기사별 번역과 달리 한 시장의 공통 테마와 상충 신호를 한 호출로 분석한다.
호출 수는 기사 수가 아니라 보고서에 포함된 시장 수에 비례한다.
"""

import json
import logging
import random
import re
from pathlib import Path
from typing import Any

from telegram_bot.llm.backends import LLMBackend

logger = logging.getLogger(__name__)

_VALIDATION_ATTEMPTS = 2
# `"analysis"` 값의 시작 자리. 끝은 다음 키가 열리는 자리로 찾는다 — 본문 안
# 따옴표를 escape하지 못해 깨진 응답이라 마지막 따옴표를 믿을 수 없다.
_ANALYSIS_OPEN = re.compile(r'"analysis"\s*:\s*"')
_ANALYSIS_CLOSE = re.compile(r'"\s*,\s*"(?:highlights|evaluations)"')


def _salvage_analysis(raw: str) -> str:
    """JSON이 통째로 깨졌을 때 본문만 건진다.

    실측(2026-09-17 00시·09시 US·KR)에서 모델이 제목을 옮기며 문자열 안
    큰따옴표를 escape하지 않아 `Expecting ',' delimiter`로 파싱이 깨졌다.
    그 한 글자 때문에 멀쩡한 400~500자 본문까지 버려지고 보고서가 원문 제목
    나열로 떨어졌다. **비싼 것은 analysis이고 highlight는 그 근거 목록이라는
    기존 판단을 top-level 파싱 실패에도 그대로 적용한다.**

    건지지 못하면 빈 문자열을 돌려준다 — 그때는 원문 제목 나열이 낫다.
    """
    opened = _ANALYSIS_OPEN.search(raw)
    if opened is None:
        return ""
    rest = raw[opened.end():]
    closed = _ANALYSIS_CLOSE.search(rest)
    if closed is not None:
        body = rest[: closed.start()]
    else:
        # 뒤가 통째로 없는 응답이다. 마지막 문장 끝까지만 남긴다 — 반 토막 문장을
        # 보고서에 싣지 않는다.
        cut = rest.rfind(".")
        if cut < 0:
            return ""
        body = rest[: cut + 1]
    body = body.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")
    return " ".join(body.split()).strip()


class NewsReportError(RuntimeError):
    """Raised when a market report cannot be produced for a market."""


class NewsReportAnalyzer:
    def __init__(
        self,
        backend: LLMBackend,
        prompt_file: Path,
        num_predict: int,
        max_highlights: int,
        min_highlights: int = 1,
        highlight_ratio: float = 1.0,
    ):
        self._backend = backend
        self._num_predict = num_predict
        self._max_highlights = max(1, max_highlights)
        self._min_highlights = max(1, min(min_highlights, self._max_highlights))
        self._highlight_ratio = highlight_ratio
        # 프롬프트에 JSON 예시가 들어 있어 str.format을 쓰면 중괄호가 깨진다.
        # 개수는 호출마다 달라지므로 원본을 두고 analyze에서 치환한다.
        self._prompt_template = prompt_file.read_text(encoding="utf-8")

    def _highlight_limit(self, article_count: int) -> int:
        """뽑을 근거 기사 수를 수집량에 비례시킨다.

        고정 8이면 수집이 얇은 시장에서 무리한 요구가 된다 — 실측(2026-09-02
        한국)에서 11건을 주고 8건을 고르라고 했다. 전체의 73%는 선별이 아니라
        목록 복사이고, 채울 것이 모자라면 모델이 없는 것을 만든다(title 누락,
        같은 index 반복). 프롬프트가 "최대"라고 말해도 제시된 숫자가 목표가 된다.
        """
        scaled = round(article_count * self._highlight_ratio)
        return max(self._min_highlights, min(self._max_highlights, scaled))

    def analyze(
        self,
        market: str,
        window: str,
        headlines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """헤드라인 목록에서 시장상황과 근거 기사를 만든다(블로킹)."""
        if not headlines:
            raise NewsReportError("no headlines to analyze")

        # 탐색 기사가 근거에 뽑히지 않아도 평가를 받을 통로를 확보한다.
        # 전체 10개 중 최대 절반만 우선 배정하고 나머지는 전체 후보에서 무작위로 뽑는다.
        exploring = [item["index"] for item in headlines if item.get("exploration")]
        sample_indexes = random.sample(exploring, min(5, len(exploring)))
        remaining = [item["index"] for item in headlines if item["index"] not in sample_indexes]
        sample_indexes += random.sample(remaining, min(10 - len(sample_indexes), len(remaining)))
        # 모델에게 선별 경로를 노출하면 중요도 평가가 그 정보에 끌릴 수 있다.
        articles = [{key: value for key, value in item.items() if key != "exploration"}
                    for item in headlines]
        payload = {"market": market, "window": window, "articles": articles,
                   "evaluation_indexes": sample_indexes}
        user_prompt = json.dumps(payload, ensure_ascii=False)
        valid_indexes = {item["index"] for item in headlines}
        limit = self._highlight_limit(len(headlines))
        prompt = self._prompt_template.replace("{max_highlights}", str(limit))
        for attempt in range(1, _VALIDATION_ATTEMPTS + 1):
            try:
                raw = self._backend.generate(
                    system_prompt=prompt,
                    user_prompt=user_prompt,
                    max_tokens=self._num_predict,
                    temperature=0.2,
                )
            except Exception as exc:
                # 전송 계층의 재시도는 ResilientBackend가 담당한다. 여기서는
                # 정상 응답의 JSON 형식·스키마가 잘못된 경우에만 다시 요청한다.
                raise NewsReportError(str(exc)) from exc

            # 마지막 시도에서는 근거 기사 한 줄이 어긋났다고 보고서를 버리지
            # 않는다. 비싼 것은 analysis이고 highlight는 그 근거 목록이다.
            # 실측(2026-09-02 CN, 하루 세 번)에서 8건 중 하나가 title이 없거나
            # index가 겹쳐 400~500자 본문이 통째로 버려지고 원문 제목만 남았다.
            last = attempt == _VALIDATION_ATTEMPTS
            try:
                if not raw.strip():
                    raise NewsReportError("empty news report response content")
                result = self._parse(
                    raw, valid_indexes=valid_indexes, limit=limit, salvage=last
                )
                result["evaluations"] = [
                    row for row in result["evaluations"] if row["index"] in sample_indexes
                ]
                return result
            except NewsReportError:
                if last:
                    raise
                logger.warning(
                    "[NEWS REPORT] %s 응답 검증 실패, 한 번 다시 요청합니다",
                    market,
                    exc_info=True,
                )

        raise AssertionError("unreachable")

    def _parse(
        self,
        raw: str,
        *,
        valid_indexes: set[int],
        limit: int,
        salvage: bool = False,
    ) -> dict[str, Any]:
        # 모델이 정상 JSON 뒤에 설명이나 두 번째 답을 덧붙여도 첫 객체만 쓴다.
        # raw_decode는 첫 객체가 끝난 위치까지만 읽으므로 후행 텍스트를 무시한다.
        start = raw.find("{")
        if start < 0:
            raise NewsReportError(
                f"news report JSON parse failed (no JSON object); raw_chars={len(raw)}"
            )
        try:
            data, _ = json.JSONDecoder().raw_decode(raw, start)
        except json.JSONDecodeError as exc:
            # 마지막 시도에서는 본문만이라도 건진다. 다시 요청할 기회가 없고,
            # 깨진 자리는 대개 제목 안의 따옴표 하나다.
            if salvage:
                analysis = _salvage_analysis(raw)
                if analysis:
                    logger.warning(
                        "[NEWS REPORT] JSON이 깨져 analysis만 건진다: "
                        "%d자 (%s); raw_chars=%d",
                        len(analysis), exc, len(raw),
                    )
                    return {"analysis": analysis, "highlights": [], "evaluations": []}
            # 원문은 남기지 않는다. 길이만으로도 잘림 여부는 판단할 수 있다.
            raise NewsReportError(
                f"news report JSON parse failed ({exc}); raw_chars={len(raw)}"
            ) from exc
        if not isinstance(data, dict):
            raise NewsReportError("news report JSON must be an object")

        analysis = data.get("analysis")
        highlights = data.get("highlights")
        if not isinstance(analysis, str):
            raise NewsReportError("news report analysis must be a string")
        if not isinstance(highlights, list):
            raise NewsReportError("news report highlights must be a list")

        parsed: list[dict[str, Any]] = []
        seen: set[int] = set()
        dropped: list[str] = []
        for row in highlights[:limit]:
            try:
                parsed.append(self._parse_highlight(row, valid_indexes, seen))
            except NewsReportError as error:
                if not salvage:
                    raise
                # 버린 건수를 남긴다. 남기지 않으면 근거 기사가 조용히 계속
                # 사라져도 알 방법이 없다.
                dropped.append(str(error))
        if dropped:
            logger.warning(
                "[NEWS REPORT] 근거 기사 %d/%d건을 버리고 보고서를 남긴다: %s",
                len(dropped),
                len(dropped) + len(parsed),
                "; ".join(dropped),
            )
        if salvage and not analysis.strip() and not parsed:
            # 본문도 없고 근거도 다 버렸으면 남길 것이 없다. 빈 섹션보다
            # 원문 제목 나열(format_market_section의 fallback)이 낫다.
            raise NewsReportError("news report has neither analysis nor highlights")
        # 학습용 부가 응답 실패로 사용자 보고서를 재요청하지 않는다.
        evaluations = []
        evaluation_seen = {row["index"] for row in parsed}
        rows = data.get("evaluations", [])
        if isinstance(rows, list):
            for row in rows[:10]:
                if not isinstance(row, dict):
                    continue
                index, impact = row.get("index"), row.get("impact")
                if (type(index) is not int or index not in valid_indexes
                        or index in evaluation_seen or impact not in ("high", "medium", "low")):
                    continue
                evaluation_seen.add(index)
                evaluations.append({"index": index, "impact": impact})
        logger.info("[NEWS REPORT] 학습용 추가 평가 %d건", len(evaluations))
        return {"analysis": analysis.strip(), "highlights": parsed, "evaluations": evaluations}

    @staticmethod
    def _parse_highlight(
        row: Any,
        valid_indexes: set[int],
        seen: set[int],
    ) -> dict[str, Any]:
        if not isinstance(row, dict):
            raise NewsReportError("news report highlight must be an object")
        index = row.get("index")
        if not isinstance(index, int) or index not in valid_indexes:
            raise NewsReportError(f"news report highlight index is unknown: {index!r}")
        if index in seen:
            raise NewsReportError(f"news report highlight index repeats: {index}")
        seen.add(index)

        title = row.get("title")
        if not isinstance(title, str) or not title.strip():
            raise NewsReportError("news report highlight missing title")
        sentiment = row.get("sentiment")
        if not isinstance(sentiment, (int, float)) or not -1 <= sentiment <= 1:
            raise NewsReportError("news report highlight sentiment must be between -1 and 1")
        impact = row.get("impact")
        if impact not in ("high", "medium", "low"):
            raise NewsReportError("news report highlight impact must be high, medium, or low")
        codes = row.get("mentioned_stocks")
        if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
            raise NewsReportError("news report highlight mentioned_stocks must be strings")

        return {
            "index": index,
            "title": title.strip(),
            "sentiment": float(sentiment),
            "impact": impact,
            "mentioned_stocks": [code.strip() for code in codes if code.strip()],
        }
