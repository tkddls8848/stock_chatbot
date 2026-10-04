"""마지막 보고 이후 모은 기사 제목을 시장상황 보고서로 추론한다.

기사별 번역과 달리 한 시장의 공통 테마와 상충 신호를 한 호출로 분석한다.
호출 수는 기사 수가 아니라 검토한 시장 수에 비례한다.

**모델은 발행 여부도 함께 판정한다.** 직전 발행분(`previous`)을 입력으로 받아
이번 묶음이 그 판단을 바꾸거나 진전시키는지 보고, 아니면 `publish`를 false로
둔다. 보류한 기사는 버려지지 않고 다음 구간이 더 두꺼운 재료로 다시 본다.
"""

import hashlib
import json
import logging
import random
import re
import unicodedata
from pathlib import Path
from typing import Any

from services.telegram_bot.llm.backends import LLMBackend
from services.telegram_bot.llm.terminology import read_prompt

logger = logging.getLogger(__name__)

_VALIDATION_ATTEMPTS = 2
# 응답 형식을 디코딩 단계에서 강제한다. 실측(2026-09-21, 스모크
# `test_cloudflare_json_mode_smoke.py`)에서 이 모델이 스키마를 지켰고, 제목의
# 큰따옴표가 이스케이프되어 원문 그대로 살아왔다.
#
# **비용은 0이다.** 같은 입력을 구조화 없이/있이 불렀을 때 입력 토큰이 정확히
# 같았다(3,781/7,399). Cloudflare는 스키마를 입력 토큰으로 과금하지 않고,
# 제약 디코딩 자체의 가산도 없다 — 네 점이
# `neurons ≈ 0.00463×입력 + 0.0304×출력`에 맞고 남는 몫이 없다. 처음 잰
# +41.5%는 입력이 250토큰뿐인 호출의 출력 길이 편차였다.
#
# **모델을 바꾸면(`CLOUDFLARE_MODEL`) 그 스모크를 다시 돌린다.** 지원하지 않는
# 모델에 이 필드를 실으면 400으로 보고서가 통째로 실패한다. Cloudflare 문서가
# 지원 목록에 올려 둔 모델이 실제로는 받지 않은 전례가 있어 문서로 갈음하지 않는다.
#
# **`uniqueItems`·`minLength`를 쓰지 않는다.** 실측된 결함 둘(같은 index 반복,
# 빈 title)을 스키마로 막고 싶지만 `uniqueItems`는 객체 전체가 같을 때만 중복이라
# title이 다른 같은 index를 걸러 주지 않고, `minLength`는 이 모델의 제약 디코딩이
# 받는지 확인할 방법이 실호출뿐이다. 지원하지 않는 필드를 실으면 400으로 보고서가
# 통째로 실패한다(문서로 갈음하지 않는다는 위 규칙과 같은 이유다). 그래서 둘 다
# 프롬프트로 지시하고 파서가 그 행만 버린다.
_IMPACT_ENUM = {"type": "string", "enum": ["high", "medium", "low"]}
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "publish": {"type": "boolean"},
        "hold_reason": {"type": "string"},
        "analysis": {"type": "string"},
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "article_id": {"type": "string"},
                    "source_title": {"type": "string"},
                    "title": {"type": "string"},
                    "sentiment": {"type": "number", "minimum": -1, "maximum": 1},
                    "impact": _IMPACT_ENUM,
                    "mentioned_stocks": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["article_id", "source_title", "title", "sentiment", "impact", "mentioned_stocks"],
            },
        },
        "evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "article_id": {"type": "string"},
                    "source_title": {"type": "string"},
                    "impact": _IMPACT_ENUM,
                },
                "required": ["article_id", "source_title", "impact"],
            },
        },
    },
    "required": ["publish", "hold_reason", "analysis", "highlights", "evaluations"],
}
# OpenAI 호환 경로(`/ai/v1/chat/completions`)를 쓰므로 OpenAI식 봉투를 쓴다.
# 스모크에서는 Cloudflare식 봉투도 통했지만, 엔드포인트와 같은 규격을 따른다.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "news_report", "schema": RESPONSE_SCHEMA},
}
# `"analysis"` 값의 시작 자리. 끝은 다음 키가 열리는 자리로 찾는다 — 본문 안
# 따옴표를 escape하지 못해 깨진 응답이라 마지막 따옴표를 믿을 수 없다.
_ANALYSIS_OPEN = re.compile(r'"analysis"\s*:\s*"')
_ANALYSIS_CLOSE = re.compile(r'"\s*,\s*"(?:highlights|evaluations)"')


def _salvage_analysis(raw: str) -> str:
    """JSON이 통째로 깨졌을 때 본문만 건진다.

    **구조화 출력을 켠 뒤에도 남는다.** 스키마는 모양을 강제할 뿐이라
    `max_tokens`에서 잘린 응답은 여전히 깨진 JSON이고, 그 절단은 이 저장소가
    실제로 겪은 실패다(2026-09-17 06시 CN·US·KR, 03시 US).


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


def _comparable_title(value: Any, publisher: str = "") -> str:
    """원문 제목 대조용 정규화. 모델이 복사하며 바꾸기 쉬운 것만 맞춘다.

    전각·반각(NFKC)과 공백, 그리고 **그 기사의 확인된 매체명**과 정확히 같은 끝의
    " - 매체명" 꼬리. 아무 하이픈 뒤나 자르지 않는다 — "정책 - 시행 전"과 "정책 - 시행 후"가
    같아지면 안 된다. article_id가 함께 맞아야 하므로 이 정규화로 다른 기사에 붙지 않는다.
    """
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()
    if publisher:
        suffix = " - " + re.sub(r"\s+", " ", unicodedata.normalize("NFKC", publisher)).strip()
        while text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)].rstrip()
    return text


def _is_korean_title(title: str) -> bool:
    """글자의 절반 이상이 한글이면 한국어 제목이다(한자 약칭 "李·美·中"이 섞여도)."""
    letters = [char for char in title if char.isalpha()]
    hangul = sum("가" <= char <= "힣" for char in letters)
    return bool(letters) and hangul * 2 >= len(letters)


def _original_title(title: str, publisher: str = "") -> str:
    """공개용 원문 제목. 그 기사의 확인된 매체명 꼬리(" - 매체명")만 떼고 공백을 고른다."""
    text = " ".join(str(title).split())
    suffix = " - " + " ".join(publisher.split()) if publisher.strip() else ""
    while suffix and text.endswith(suffix) and len(text) > len(suffix):
        text = text[: -len(suffix)].rstrip()
    return text


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
        self._prompt_template = read_prompt(prompt_file)

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
        previous: dict[str, Any] | None = None,
        must_publish: bool = False,
    ) -> dict[str, Any]:
        """헤드라인 목록에서 발행 판정·시장상황·근거 기사를 만든다(블로킹).

        `previous`는 이 시장에 마지막으로 **발행한** 보고서다. 사용자가 읽은
        마지막 글이라 비교 대상이 되고, 없으면 비교 없이 발행한다.
        `must_publish`는 보류 상한에 닿아 판정과 무관하게 발행하는 경우다.
        """
        if not headlines:
            raise NewsReportError("no headlines to analyze")

        # index는 서버 내부 위치다. 모델은 ID와 원문 제목을 복사하고,
        # 서버는 둘이 같은 입력 기사를 가리키는지 확인한 뒤 위치를 복원한다.
        known_articles: dict[str, dict[str, Any]] = {}
        indexes = set()
        skipped = 0
        for item in headlines:
            article_id = item.get("article_id")
            index = item.get("index")
            # RSS ID에는 긴 URL이 들어갈 수 있다. URL을 모델이 복사하게 하지
            # 않고, 순서와 무관한 짧은 ID를 쓴다.
            identity = ("news-" + hashlib.sha256(article_id.encode("utf-8")).hexdigest()[:16]
                        if isinstance(article_id, str) and article_id.strip() else "")
            if (not identity or identity in known_articles
                    or type(index) is not int or index < 0 or index in indexes
                    or not isinstance(item.get("title"), str) or not item["title"].strip()):
                # 그 기사만 뺀다. 한 건 때문에 시장 분석 전체를 멈추지 않는다 — 호출자
                # (`news/report.py`의 `_reportable`)가 이미 거르므로 여기는 방어선이다.
                skipped += 1
                continue
            known_articles[identity] = item
            indexes.add(index)
        if skipped:
            logger.warning("[NEWS REPORT] %s 입력 기사 %d건을 제목·ID 문제로 뺐다", market, skipped)
        if not known_articles:
            raise NewsReportError("news report has no valid input article")
        exploring = [identity for identity, item in known_articles.items() if item.get("exploration")]
        sample_ids = random.sample(exploring, min(5, len(exploring)))
        remaining = [identity for identity in known_articles if identity not in sample_ids]
        sample_ids += random.sample(remaining, min(10 - len(sample_ids), len(remaining)))
        # 모델에게 선별 경로나 서버 내부 위치를 노출하지 않는다.
        # 모델에 보내는 칸은 정해 둔다. 매체명(publisher)·선별 경로·서버 위치는 보내지 않는다 —
        # 분석에 매체명은 필요 없다(운영자 결정 2026-10-02).
        articles = [{"article_id": identity,
                     **{key: item[key] for key in ("title", "source", "published_at") if key in item}}
                    for identity, item in known_articles.items()]
        payload = {"market": market, "window": window, "articles": articles,
                   "previous": previous or None, "must_publish": bool(must_publish),
                   "evaluation_article_ids": sample_ids}
        user_prompt = json.dumps(payload, ensure_ascii=False)
        logger.debug("[NEWS REPORT] %s request=%s", market, user_prompt)
        limit = self._highlight_limit(len(known_articles))
        prompt = self._prompt_template.replace("{max_highlights}", str(limit))
        for attempt in range(1, _VALIDATION_ATTEMPTS + 1):
            try:
                raw = self._backend.generate(
                    system_prompt=prompt,
                    user_prompt=user_prompt,
                    max_tokens=self._num_predict,
                    temperature=0.2,
                    response_format=RESPONSE_FORMAT,
                )
            except Exception as exc:
                # 전송 계층의 재시도는 ResilientBackend가 담당한다. 여기서는
                # 정상 응답의 JSON 형식·스키마가 잘못된 경우에만 다시 요청한다.
                raise NewsReportError(str(exc)) from exc

            logger.debug("[NEWS REPORT] %s attempt=%d response=%s", market, attempt, raw)

            # `salvage`는 "다시 물을 기회가 없다"는 뜻이다. 어긋난 근거 행은
            # 어느 시도에서든 그 행만 버리고, 유효한 근거가 하나도 남지 않았을
            # 때만 한 번 다시 묻는다(`_parse`). 깨진 JSON에서 본문만 건지는
            # 것도 마지막 시도에서만 한다.
            last = attempt == _VALIDATION_ATTEMPTS
            try:
                if not raw.strip():
                    raise NewsReportError("empty news report response content")
                result = self._parse(
                    raw,
                    known_articles=known_articles,
                    limit=limit,
                    salvage=last,
                    must_publish=bool(must_publish),
                )
                sample_indexes = {known_articles[identity]["index"] for identity in sample_ids}
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
        known_articles: dict[str, dict[str, Any]],
        limit: int,
        salvage: bool = False,
        must_publish: bool = False,
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
                    # 건진 본문이 있으면 발행한다. 판정 필드까지 깨진 응답을
                    # 보류로 읽으면 쓸 만한 글을 조용히 버리게 된다.
                    return {"publish": True, "hold_reason": "", "analysis": analysis,
                            "highlights": [], "evaluations": []}
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

        # 판정 필드가 없거나 모양이 틀리면 발행으로 읽는다. 보류는 사용자에게
        # 한 구간의 침묵이라, 필드 하나가 빠졌다는 이유로 침묵하면 안 된다.
        publish = data.get("publish")
        if not isinstance(publish, bool):
            if publish is not None:
                logger.warning("[NEWS REPORT] publish 값이 bool이 아니라 발행으로 읽는다: %r", publish)
            publish = True
        if must_publish:
            publish = True
        hold_reason = data.get("hold_reason")
        hold_reason = hold_reason.strip()[:200] if isinstance(hold_reason, str) else ""

        parsed: list[dict[str, Any]] = []
        seen: set[int] = set()
        dropped: list[str] = []
        # 보류라면 근거를 읽지 않는다. 표시하지 않을 목록을 검사하느라 보고서를
        # 버리게 되고, 버려질 근거가 학습 표본의 자리까지 차지한다.
        for row in highlights[:limit] if publish else []:
            try:
                parsed.append(self._parse_highlight(row, known_articles, seen))
            except NewsReportError as error:
                # **검사는 그대로 엄격하다.** 어긋난 행은 결과에도 NewsLog에도
                # 사전선별 라벨에도 들어가지 않는다 — 통과시키는 것이 아니라
                # 그 행만 버린다. 비싼 것은 analysis이고 highlight는 그 근거
                # 목록이라, 한 줄 때문에 400~500자 본문을 버리지 않는다.
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
        if dropped and not parsed and not salvage:
            # 유효한 근거가 하나도 남지 않은 응답은 근거 목록이 아니다 — 모델이
            # ID를 통째로 지어냈거나 한 기사만 되풀이한 것이다. 라벨 공급원이
            # 이 호출 하나뿐이라 그때는 한 번 다시 묻는 값어치가 있다.
            # **행 하나가 어긋났다고 다시 묻지는 않는다.** 실측(2026-09-23~25)의
            # `highlight index repeats: 1` 4건·`missing title` 1건은 전부 남은
            # 근거만으로 충분한 응답이었는데도 같은 시장에 호출이 두 번 나갔다.
            raise NewsReportError(
                f"news report kept no valid highlight of {len(dropped)}: "
                + "; ".join(dropped)
            )
        if publish and not analysis.strip() and not parsed:
            # 발행이라면서 본문도 근거도 없다. 빈 섹션을 보내지 않는다 —
            # 재요청하거나(중간 시도) 원문 제목 나열로 떨어뜨린다(마지막 시도).
            raise NewsReportError("news report has neither analysis nor highlights")
        # 학습용 부가 응답 실패로 사용자 보고서를 재요청하지 않는다.
        evaluations = []
        evaluation_seen = {row["index"] for row in parsed}
        rows = data.get("evaluations", [])
        if isinstance(rows, list):
            for row in rows[:10]:
                if not isinstance(row, dict):
                    continue
                try:
                    index = self._article_index(row, known_articles)
                except NewsReportError as error:
                    logger.warning("[NEWS REPORT] 학습용 평가를 버린다: %s", error)
                    continue
                impact = row.get("impact")
                if index in evaluation_seen or impact not in ("high", "medium", "low"):
                    continue
                evaluation_seen.add(index)
                evaluations.append({"index": index, "impact": impact})
        logger.info("[NEWS REPORT] 학습용 추가 평가 %d건", len(evaluations))
        if not publish:
            # 보류분은 사용자가 보지 않는다. 본문과 근거를 들고 있으면 다음
            # 구간이 그것을 발행한 글로 착각한다 — 평가만 남긴다.
            return {"publish": False, "hold_reason": hold_reason, "analysis": "",
                    "highlights": [], "evaluations": evaluations}
        return {"publish": True, "hold_reason": "", "analysis": analysis.strip(),
                "highlights": parsed, "evaluations": evaluations}

    @staticmethod
    def _article_index(row: dict, known_articles: dict[str, dict[str, Any]]) -> int:
        article_id = row.get("article_id")
        if not isinstance(article_id, str) or article_id not in known_articles:
            raise NewsReportError(f"news report article ID is unknown: {article_id!r}")
        article = known_articles[article_id]
        publisher = str(article.get("publisher") or "")
        if _comparable_title(row.get("source_title"), publisher) != _comparable_title(article["title"], publisher):
            raise NewsReportError(
                f"news report source title mismatch for {article_id!r}: "
                f"expected={article['title']!r}, received={row.get('source_title')!r}"
            )
        return article["index"]

    @staticmethod
    def _parse_highlight(
        row: Any,
        known_articles: dict[str, dict[str, Any]],
        seen: set[int],
    ) -> dict[str, Any]:
        if not isinstance(row, dict):
            raise NewsReportError("news report highlight must be an object")
        index = NewsReportAnalyzer._article_index(row, known_articles)
        if index in seen:
            raise NewsReportError(f"news report highlight index repeats: {index}")

        # 한국어 원문은 원문 제목을 그대로 쓴다. 이미 한국어인 제목을 모델이 다시 쓰다가 "李대통령"을
        # "이재용 회장"으로 바꿨다(2026-10-04 연합뉴스). 공개 중이던 한국어 기사 250건 중 186건이 그렇게
        # 다시 쓰였고, 다른 기사의 제목이 붙은 것도 있었다. 번역이 필요한 외국어 기사만 모델 title을 쓴다.
        article = known_articles[row["article_id"]]
        if _is_korean_title(article["title"]):
            title = _original_title(article["title"], str(article.get("publisher") or ""))
        else:
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

        seen.add(index)
        return {
            "index": index,
            "title": title.strip(),
            "sentiment": float(sentiment),
            "impact": impact,
            "mentioned_stocks": [code.strip() for code in codes if code.strip()],
        }
