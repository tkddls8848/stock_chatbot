"""구조화 출력(JSON 스키마 강제)이 **이 모델에서 실제로 도는지** 재는 스모크.

기본 실행(`python -m pytest -q`)에서는 건너뛴다. 자격증명과 무료 할당량을
소비하므로 명시적으로 켤 때만 돈다. `-s`를 붙여야 측정값이 보인다.

```powershell
$env:RUN_CLOUDFLARE_SMOKE='1'
python -m pytest -q -s -m cloudflare_smoke telegram_bot/tests/test_cloudflare_json_mode_smoke.py
```

**왜 문서를 믿지 않고 재는가.** Cloudflare 문서의 JSON 모드 지원 모델 목록에
실제로는 받지 않는 모델이 올라와 있던 전례가 있다(cloudflare-docs #27786,
목록을 정정한 #33406). 게다가 이 저장소가 쓰는 `@cf/qwen/qwen3-30b-a3b-fp8`은
추론 모델이라 `backends.py`가 이미 `/no_think`로 우회하고 있어, 구조화 디코딩이
그 우회와 어떻게 겹치는지도 문서로는 알 수 없다.

재는 것은 넷이다.

1. **봉투 모양.** OpenAI 호환 경로(`/ai/v1/chat/completions`)가 OpenAI식
   `{"type":"json_schema","json_schema":{"name":...,"schema":{...}}}`을 받는지,
   Cloudflare 자체 문서식 `{"type":"json_schema","json_schema":{<스키마>}}`를
   받는지. 둘 다 시도해 어느 쪽이 통하는지 남긴다.
2. **스키마 준수.** 받은 JSON이 선언한 모양 그대로인지.
3. **따옴표 이스케이프.** 제목에 큰따옴표가 든 기사를 옮기게 시킨다. 2026-09-17
   실측 사고가 정확히 이것이었고(`Expecting ',' delimiter`로 본문까지 버려짐),
   지금은 프롬프트가 「」로 바꿔 쓰라고 강요해 제목이 원문과 달라진다.
   구조화 출력의 실익은 이 우회를 지울 수 있느냐다.
4. **비용.** 같은 입력을 구조화 없이 한 번 더 불러 응답의 `neurons`를 견준다.
   Cloudflare가 과금 단위를 응답에 담아 주므로 추정하지 않는다.
"""

import json
import logging
import os
import re

import pytest

from telegram_bot.core.config import (
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_AI_BASE_URL,
    CLOUDFLARE_API_TOKEN,
    CLOUDFLARE_MODEL,
    NEWS_REPORT_TIMEOUT,
)
from telegram_bot.llm.backends import CloudflareWorkersAIBackend, LLMBackendError

pytestmark = [
    pytest.mark.cloudflare_smoke,
    pytest.mark.skipif(
        os.environ.get("RUN_CLOUDFLARE_SMOKE") != "1",
        reason="RUN_CLOUDFLARE_SMOKE=1 이 아닐 때는 실제 API를 호출하지 않는다",
    ),
    pytest.mark.skipif(
        not (CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN),
        reason="CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN 미설정",
    ),
]

# 3시간 보고서 응답의 축소판이다. 실제 스키마를 그대로 쓰지 않는 것은, 여기서
# 재는 것이 보고서 품질이 아니라 **형식 강제가 걸리느냐**이기 때문이다.
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "publish": {"type": "boolean"},
        "analysis": {"type": "string"},
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "title": {"type": "string"},
                    "sentiment": {"type": "number", "minimum": -1, "maximum": 1},
                    "impact": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["index", "title", "sentiment", "impact"],
            },
        },
    },
    "required": ["publish", "analysis", "highlights"],
}

# 봉투 두 벌. 어느 쪽을 받는지가 이 스모크의 첫 번째 답이다.
ENVELOPES = {
    "openai": {
        "type": "json_schema",
        "json_schema": {"name": "news_report", "schema": REPORT_SCHEMA},
    },
    "cloudflare": {"type": "json_schema", "json_schema": REPORT_SCHEMA},
}

SYSTEM_PROMPT = (
    "당신은 시장 뉴스 분석가다. 주어진 기사 제목을 읽고 한국어로 짧은 시장상황 "
    "판단을 쓰고, 근거가 된 기사를 고른다. analysis는 100자 내외로 쓴다. "
    "title은 기사 제목을 한국어로 옮긴 것이며 원문의 인용부호를 그대로 살린다."
)

# 제목 안의 큰따옴표가 이 시험의 핵심이다. 지금 프롬프트는 이것을 「」로 바꾸라고
# 강요하고 있고, 구조화 출력이 통하면 그 규칙을 지울 수 있다.
USER_PROMPT = json.dumps(
    {
        "market": "US",
        "articles": [
            {
                "index": 0,
                "title": 'Apple says "Vision Pro" demand beat expectations, shares rise 3%',
                "source": "Reuters",
            },
            {
                "index": 1,
                "title": "Fed holds rates steady, signals one cut in 2026",
                "source": "Bloomberg",
            },
        ],
    },
    ensure_ascii=False,
)

MAX_TOKENS = 1024
_NEURONS = re.compile(r"neurons=([0-9.]+)")


def _backend() -> CloudflareWorkersAIBackend:
    return CloudflareWorkersAIBackend(
        account_id=CLOUDFLARE_ACCOUNT_ID,
        api_token=CLOUDFLARE_API_TOKEN,
        model=CLOUDFLARE_MODEL,
        base_url=CLOUDFLARE_AI_BASE_URL,
        timeout=NEWS_REPORT_TIMEOUT,
    )


def _call(caplog, response_format=None) -> tuple[str, float | None]:
    """한 번 부르고 본문과 그 호출이 태운 Neurons를 돌려준다."""
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="telegram_bot.llm.backends"):
        content = _backend().generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=USER_PROMPT,
            max_tokens=MAX_TOKENS,
            temperature=0.2,
            response_format=response_format,
        )
    found = _NEURONS.search(caplog.text)
    return content, float(found.group(1)) if found else None


def _conforms(data: object) -> list[str]:
    """스키마 위반을 모아 돌려준다. 빈 목록이면 준수다."""
    problems = []
    if not isinstance(data, dict):
        return [f"최상위가 객체가 아니다: {type(data).__name__}"]
    if not isinstance(data.get("publish"), bool):
        problems.append(f"publish가 bool이 아니다: {data.get('publish')!r}")
    if not isinstance(data.get("analysis"), str):
        problems.append(f"analysis가 문자열이 아니다: {data.get('analysis')!r}")
    highlights = data.get("highlights")
    if not isinstance(highlights, list):
        return problems + [f"highlights가 배열이 아니다: {highlights!r}"]
    for row in highlights:
        if not isinstance(row, dict):
            problems.append(f"highlight가 객체가 아니다: {row!r}")
            continue
        if not isinstance(row.get("index"), int) or isinstance(row.get("index"), bool):
            problems.append(f"index가 정수가 아니다: {row.get('index')!r}")
        if not isinstance(row.get("title"), str) or not row["title"].strip():
            problems.append(f"title이 비었다: {row.get('title')!r}")
        sentiment = row.get("sentiment")
        if not isinstance(sentiment, (int, float)) or not -1 <= sentiment <= 1:
            problems.append(f"sentiment가 -1~1 밖이다: {sentiment!r}")
        if row.get("impact") not in ("high", "medium", "low"):
            problems.append(f"impact가 enum 밖이다: {row.get('impact')!r}")
    return problems


def test_json_schema_is_enforced_by_this_model(caplog):
    """봉투 두 벌 중 하나라도 통하면 통과. 어느 쪽이 통했는지 출력에 남긴다."""
    results = {}
    for name, envelope in ENVELOPES.items():
        try:
            content, neurons = _call(caplog, envelope)
        except LLMBackendError as error:
            results[name] = f"거부됨 — {error}"
            continue
        try:
            data = json.loads(content)
        except json.JSONDecodeError as error:
            results[name] = f"JSON 파싱 실패 — {error}; 앞 200자: {content[:200]!r}"
            continue
        problems = _conforms(data)
        results[name] = {
            "neurons": neurons,
            "problems": problems,
            "analysis_chars": len(str(data.get("analysis") or "")),
            "highlights": len(data.get("highlights") or []),
            "titles": [row.get("title") for row in data.get("highlights") or []
                       if isinstance(row, dict)],
        }

    print("\n=== 구조화 출력 측정 ===")
    print(f"모델: {CLOUDFLARE_MODEL}")
    for name, result in results.items():
        print(f"[{name}] {result}")

    working = [
        name for name, result in results.items()
        if isinstance(result, dict) and not result["problems"]
    ]
    assert working, (
        f"{CLOUDFLARE_MODEL}에서 구조화 출력이 통하지 않는다. "
        f"결과: {results}"
    )


def test_a_quoted_headline_survives_without_the_bracket_workaround(caplog):
    """제목의 큰따옴표가 이스케이프되어 살아 오는지 본다.

    통하면 프롬프트의 「」 치환 규칙을 지울 수 있다. 지금은 그 규칙 때문에
    보고서에 실리는 제목이 원문과 다르다.
    """
    envelope = ENVELOPES["openai"]
    try:
        content, _ = _call(caplog, envelope)
    except LLMBackendError:
        pytest.skip("이 봉투를 모델이 거부했다. 위 시험이 어느 봉투가 통하는지 알려준다")

    data = json.loads(content)
    titles = " ".join(
        str(row.get("title") or "") for row in data.get("highlights") or []
        if isinstance(row, dict)
    )
    print(f"\n제목들: {titles}")
    # 모델이 애플 기사를 근거로 고르지 않았으면 이 시험은 판정할 수 없다.
    if "Vision Pro" not in titles and "비전" not in titles:
        pytest.skip(f"애플 기사를 근거로 고르지 않아 판정할 수 없다: {titles}")
    assert '"' in titles or "'" in titles, (
        f"인용부호가 사라졌다. 원문 그대로 옮기지 못한다: {titles}"
    )


def test_structured_output_cost_against_the_plain_call(caplog):
    """같은 입력을 구조화 없이 한 번 더 불러 Neurons를 견준다.

    판정하지 않고 **숫자를 남긴다.** 스키마가 입력 토큰에 얹히는 만큼 늘고,
    형식 실패 재시도가 사라지는 만큼 준다 — 어느 쪽이 큰지는 운영 로그가
    답할 문제이고 여기서는 호출당 차이만 잰다.
    """
    plain_content, plain_neurons = _call(caplog)
    try:
        _, schema_neurons = _call(caplog, ENVELOPES["openai"])
    except LLMBackendError as error:
        pytest.skip(f"구조화 호출이 거부되어 비교할 수 없다: {error}")

    print("\n=== 호출당 비용 ===")
    print(f"구조화 없음: neurons={plain_neurons}")
    print(f"구조화 있음: neurons={schema_neurons}")
    if plain_neurons and schema_neurons:
        print(f"차이: {schema_neurons - plain_neurons:+.2f} "
              f"({(schema_neurons / plain_neurons - 1) * 100:+.1f}%)")
    # 구조화 없는 호출이 JSON을 돌려주리라는 보장이 없다는 것도 기록으로 남긴다.
    try:
        json.loads(plain_content)
        print("구조화 없는 응답도 이번에는 JSON이었다")
    except json.JSONDecodeError as error:
        print(f"구조화 없는 응답은 JSON이 아니었다: {error}")
