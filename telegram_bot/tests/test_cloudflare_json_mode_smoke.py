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
    NEWS_REPORT_MAX_HEADLINES,
    NEWS_REPORT_NUM_PREDICT,
    NEWS_REPORT_PROMPT_FILE,
    NEWS_REPORT_TIMEOUT,
)
from telegram_bot.llm.backends import CloudflareWorkersAIBackend, LLMBackendError
from telegram_bot.llm.news_report import RESPONSE_SCHEMA as REPORT_SCHEMA

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

# 스키마는 `news_report`가 실제로 쓰는 것을 그대로 가져온다. 사본을 두면 재는
# 것과 도는 것이 갈라져, 측정이 통과해도 운영에서 다른 스키마가 나간다.
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
_USAGE = re.compile(
    r"input_tokens=(?P<input>[0-9?]+) output_tokens=(?P<output>[0-9?]+)"
    r"(?: neurons=(?P<neurons>[0-9.]+))?"
)


def _int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def _backend() -> CloudflareWorkersAIBackend:
    return CloudflareWorkersAIBackend(
        account_id=CLOUDFLARE_ACCOUNT_ID,
        api_token=CLOUDFLARE_API_TOKEN,
        model=CLOUDFLARE_MODEL,
        base_url=CLOUDFLARE_AI_BASE_URL,
        timeout=NEWS_REPORT_TIMEOUT,
    )


def _call(
    caplog,
    response_format=None,
    *,
    system_prompt: str = SYSTEM_PROMPT,
    user_prompt: str = USER_PROMPT,
    max_tokens: int = MAX_TOKENS,
) -> tuple[str, dict[str, float | int | None]]:
    """한 번 부르고 본문과 그 호출의 usage를 돌려준다.

    Cloudflare가 입력·출력 토큰과 과금 단위를 응답에 담아 주므로 추정하지
    않는다. 늘어난 비용이 **스키마가 얹힌 입력 토큰인지 제약 디코딩
    오버헤드인지**는 이 셋을 나란히 놓아야 갈린다.
    """
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="telegram_bot.llm.backends"):
        content = _backend().generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=0.2,
            response_format=response_format,
        )
    found = _USAGE.search(caplog.text)
    if found is None:
        return content, {"input": None, "output": None, "neurons": None}
    return content, {
        "input": _int(found.group("input")),
        "output": _int(found.group("output")),
        "neurons": float(found.group("neurons")) if found.group("neurons") else None,
    }


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
            content, usage = _call(caplog, envelope)
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
            "neurons": usage["neurons"],
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
        content, _usage = _call(caplog, envelope)
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


def _report_payload(article_count: int) -> tuple[str, str]:
    """실제 보고서와 같은 모양의 프롬프트·입력을 정해진 기사 수로 만든다.

    **크기를 맞추지 않으면 답이 왜곡된다.** 스키마는 크기가 고정된 입력 토큰
    덩어리라, 입력이 작으면 증가율이 부풀고 크면 옅어진다. 기사 2건짜리 첫
    측정에서 +41.5%가 나온 것도 그 호출의 입력이 250토큰 남짓이라 스키마
    250토큰이 거의 그대로 비율이 됐기 때문이다.

    실제 호출의 입력은 2,832~3,601토큰이었다(실측 2026-09-17). 그 구간을
    양쪽에서 감싸도록 두 크기로 잰다 — 얇은 시장(40건)과 상한
    (`NEWS_REPORT_MAX_HEADLINES`). 한 점만 재면 그 점이 우연히 유리한
    자리였는지 알 수 없다.
    """
    prompt = NEWS_REPORT_PROMPT_FILE.read_text(encoding="utf-8").replace(
        "{max_highlights}", "8"
    )
    subjects = [
        "Nvidia", "Apple", "Tesla", "Samsung Electronics", "TSMC", "Alphabet",
        "Microsoft", "Amazon", "Meta", "Intel", "AMD", "Broadcom",
    ]
    verbs = [
        "beats quarterly estimates as data center demand accelerates",
        "cuts full-year outlook citing weaker consumer spending",
        "announces $2.4 billion supply agreement with a memory maker",
        "faces antitrust probe over bundled cloud licensing terms",
        "raises dividend 12% after record free cash flow",
        "delays product launch to the first quarter of next year",
    ]
    sources = ["Reuters", "Bloomberg", "CNBC", "연합뉴스", "한국경제", "Yicai"]
    articles = []
    for index in range(article_count):
        subject = subjects[index % len(subjects)]
        verb = verbs[(index // len(subjects)) % len(verbs)]
        title = f"{subject} {verb}"
        # 실제 입력에는 따옴표가 든 제목이 섞여 들어온다. 그것이 사고의 원인이었다.
        if index % 17 == 0:
            title = f'{subject} says "demand is structural", {verb}'
        articles.append({
            "index": index,
            "title": title,
            "source": sources[index % len(sources)],
            "published_at": f"{9 + index % 12:02d}:{index % 60:02d} UTC +9",
        })
    payload = {
        "market": "US",
        "window": "09:00~12:00 UTC +9",
        "articles": articles,
        "previous": {
            "window": "06:00~09:00 UTC +9",
            "published_at": "2026-09-21T09:00:00+09:00",
            "analysis": (
                "반도체 공급 계약 발표가 세 건 겹치며 장비주 중심의 상승 국면이 "
                "이어진다. 직전 보고서가 제시한 관찰 포인트였던 메모리 가격 반등은 "
                "아직 확인되지 않았다. 다음 구간에는 메모리 현물가 방향을 본다."
            ),
        },
        "must_publish": False,
        "evaluation_indexes": list(range(10)),
    }
    return prompt, json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize("article_count", [40, NEWS_REPORT_MAX_HEADLINES])
def test_structured_output_cost_at_the_real_report_size(caplog, article_count):
    """실제 보고서 크기에서 구조화 출력이 호출당 얼마를 더 태우는지 잰다.

    판정하지 않고 **숫자를 남긴다.** 늘어난 값이 스키마가 얹힌 입력 토큰이면
    입력이 큰 실제 호출에서는 옅게 희석되고, 출력 토큰이나 그 어느 쪽도 아닌
    몫이면 제약 디코딩 자체의 오버헤드다 — 셋을 나란히 찍어야 갈린다.

    맞은편 절감은 형식 실패 재시도다. `_VALIDATION_ATTEMPTS=2`라 형식이 깨지면
    그 호출을 통째로 한 번 더 태운다(실측 2026-09-17: 따옴표 파손 3건 / 하루
    최대 32회 ≈ 9%).
    """
    prompt, user_prompt = _report_payload(article_count)
    plain_content, plain = _call(
        caplog,
        system_prompt=prompt,
        user_prompt=user_prompt,
        max_tokens=NEWS_REPORT_NUM_PREDICT,
    )
    try:
        schema_content, schema = _call(
            caplog,
            ENVELOPES["openai"],
            system_prompt=prompt,
            user_prompt=user_prompt,
            max_tokens=NEWS_REPORT_NUM_PREDICT,
        )
    except LLMBackendError as error:
        pytest.skip(f"구조화 호출이 거부되어 비교할 수 없다: {error}")

    print(f"\n=== 호출당 비용 · 기사 {article_count}건 ===")
    print(f"구조화 없음: {plain}")
    print(f"구조화 있음: {schema}")
    for key in ("input", "output", "neurons"):
        before, after = plain[key], schema[key]
        if before and after:
            print(f"{key}: {before} → {after} "
                  f"({after - before:+.2f}, {(after / before - 1) * 100:+.1f}%)")

    # 두 응답이 실제로 쓸 만한 보고서인지도 같은 자리에서 본다. 싸다고 해도
    # 본문이 짧아지면 적용할 이유가 없다.
    for label, content in (("구조화 없음", plain_content), ("구조화 있음", schema_content)):
        try:
            data = json.loads(content)
        except json.JSONDecodeError as error:
            print(f"[{label}] JSON 아님 — {error}; 앞 120자: {content[:120]!r}")
            continue
        analysis = str(data.get("analysis") or "")
        print(f"[{label}] analysis {len(analysis)}자 · "
              f"highlights {len(data.get('highlights') or [])}건 · "
              f"evaluations {len(data.get('evaluations') or [])}건")
        print(f"[{label}] 본문: {analysis[:120]}")
