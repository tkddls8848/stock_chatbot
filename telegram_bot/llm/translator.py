import json
from dataclasses import dataclass
from pathlib import Path

from telegram_bot.llm.backends import LLMBackend


_RAW_EXCERPT_CHARS = 200
# 품질 하한. 프롬프트는 본문을 180~220자로 지시하므로 이 값들에 걸리는 응답은
# "짧게 나온 요약"이 아니라 요약을 하지 않은 응답이다 — 제목만 되풀이했거나
# 원문을 그대로 돌려준 경우다.
_MIN_CONTENT_CHARS = 60
# 글자(숫자·기호 제외) 중 한글 비율. 종목명·티커가 원문 표기로 남는 것은
# 프롬프트가 시키는 일이라 넉넉히 잡고, 통째로 번역되지 않은 응답만 거른다.
_MIN_HANGUL_RATIO = 0.35


class TranslationError(RuntimeError):
    """Raised when translation is required but unavailable."""


class TranslationQualityError(TranslationError):
    """봉투는 맞지만 사람이 읽을 번역이 아닌 응답.

    형식 오류(TranslationError)와 구분한다. 형식 오류는 다시 부르면 달라질 수
    있지만 이쪽은 같은 원문에 대해 대체로 다시 나오므로, 호출자가 그 기사를
    재시도 대상에서 빼야 매 주기 같은 기사에 Neurons를 태우지 않는다.
    """


def _hangul_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    hangul = sum(1 for char in letters if "\uac00" <= char <= "\ud7a3")
    return hangul / len(letters)


def _excerpt(raw: object) -> str:
    """실패한 응답의 앞부분. 줄바꿈을 접어 로그 한 줄에 들어가게 한다."""
    collapsed = " ".join(str(raw).split())
    if len(collapsed) <= _RAW_EXCERPT_CHARS:
        return collapsed
    return collapsed[:_RAW_EXCERPT_CHARS] + "…"


@dataclass(frozen=True)
class TranslationResult:
    title: str
    content: str
    mentioned_stocks: list[str]
    sentiment: float | None = None
    impact: str = ""


class TranslationService:
    def __init__(
        self,
        backend: LLMBackend,
        enabled: bool,
        prompt_dir: Path,
        num_predict: int = 512,
        temperature: float = 0.1,
    ):
        self._backend = backend
        self._enabled = enabled
        self._num_predict = num_predict
        self._temperature = temperature
        self._prompt = (prompt_dir / "global_ko.txt").read_text(encoding="utf-8")

    def translate_article(self, title: str, content: str) -> TranslationResult:
        if not self._enabled:
            return TranslationResult(title, content, [])

        # 호출 자체가 실패하면 남길 응답이 없다. except에서 이름이 비지 않게 둔다.
        raw = ""
        try:
            raw = self._backend.generate(
                system_prompt=self._prompt,
                user_prompt=f"title:\n{title}\n\ncontent:\n{content.strip() or title}",
                max_tokens=self._num_predict,
                temperature=self._temperature,
            )
            translated = self._parse_translation(raw)
            self._check_quality(translated)
            return translated
        except TranslationQualityError:
            raise
        except Exception as exc:
            # 응답의 어느 부분이 계약을 벗어났는지 로그만 보고 알 수 있어야 한다.
            # 실패한 호출도 Neurons를 이미 썼으므로, 재현을 기다리지 않고 그
            # 자리에서 응답 앞부분을 남긴다. 백엔드 오류(HTTP 등)일 때는 raw가
            # 비어 있어 기존 메시지만 남는다.
            detail = f" | raw={_excerpt(raw)}" if raw else ""
            raise TranslationError(f"{exc}{detail}") from exc

    @staticmethod
    def _check_quality(result: TranslationResult) -> None:
        """번역이 실제로 한국어 요약인지 본다.

        형식 검사(_parse_translation)를 통과해도 모델이 원문을 그대로 돌려주거나
        제목을 한 번 더 쓰는 주기가 있다. 그대로 보내면 다이제스트에 원문
        한 줄이 섞이고, news_log·prediction_log에도 그 상태로 남는다.
        """
        if len(result.content) < _MIN_CONTENT_CHARS:
            raise TranslationQualityError(
                f"translation content too short ({len(result.content)}자 < {_MIN_CONTENT_CHARS}자)"
            )
        if result.content.strip() == result.title.strip():
            raise TranslationQualityError("translation content repeats the title")
        for field_name, value in (("title", result.title), ("content", result.content)):
            ratio = _hangul_ratio(value)
            if ratio < _MIN_HANGUL_RATIO:
                raise TranslationQualityError(
                    f"translation {field_name} is not Korean (한글 비율 {ratio:.2f})"
                )

    @staticmethod
    def _parse_translation(raw: str) -> TranslationResult:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("translation JSON must be an object")

        title = data.get("title")
        content = data.get("content")
        mentioned_stocks = data.get("mentioned_stocks")
        sentiment = data.get("sentiment")
        impact = data.get("impact")

        if not isinstance(title, str) or not title.strip():
            raise ValueError("translation JSON missing title")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("translation JSON missing content")
        if not isinstance(mentioned_stocks, list) or any(
            not isinstance(code, str) for code in mentioned_stocks
        ):
            raise ValueError("translation JSON mentioned_stocks must be strings")
        if not isinstance(sentiment, (int, float)) or not -1 <= sentiment <= 1:
            raise ValueError("translation JSON sentiment must be between -1 and 1")
        if impact not in ("high", "medium", "low"):
            raise ValueError("translation JSON impact must be high, medium, or low")

        return TranslationResult(
            title=title.strip(),
            content=content.strip(),
            mentioned_stocks=[code.strip() for code in mentioned_stocks if code.strip()],
            sentiment=float(sentiment),
            impact=impact,
        )
