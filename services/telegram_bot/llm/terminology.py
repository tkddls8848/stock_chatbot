"""모델 프롬프트 앞에 붙이는 경제·정치 용어 기준(`prompts/terminology_ko.txt`).

분석기마다 같은 표를 따로 적으면 한쪽만 고쳐진다. 프롬프트를 읽는 자리는 전부 이
함수를 거친다. 앞에 붙이는 이유는 프롬프트마다 마지막에 출력 형식("JSON만")이 있어서다 —
그 지시가 마지막에 남아야 지켜진다. 웹에도 같은 이름의 파일이 있지만 별개다(모듈끼리
코드를 공유하지 않는다).
"""

from pathlib import Path

from services.telegram_bot.core.config import PROMPT_DIR

TERMINOLOGY_FILE = PROMPT_DIR / "terminology_ko.txt"


def read_prompt(path: Path) -> str:
    return TERMINOLOGY_FILE.read_text(encoding="utf-8").rstrip() + "\n\n" + path.read_text(encoding="utf-8")
