"""실제 Cloudflare Workers AI 계정을 쓰는 opt-in 스모크 테스트.

기본 실행(`python -m pytest -q`)에서는 건너뛴다. 자격증명과 무료 할당량을
소비하므로 아래처럼 명시적으로 켤 때만 돈다.

```powershell
$env:RUN_CLOUDFLARE_SMOKE='1'
python -m pytest -q -m cloudflare_smoke
```

확인 항목: 단일 JSON 파싱, 한국어 제목·본문, 숫자·종목코드 보존, 길이 준수,
응답의 usage 필드 제공 여부.
"""

import os
import re

import pytest

from shared.core.config import (
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_AI_BASE_URL,
    CLOUDFLARE_API_TOKEN,
    CLOUDFLARE_MODEL,
    CLOUDFLARE_TRANSLATION_TIMEOUT,
    PROMPT_DIR,
    TRANSLATION_NUM_PREDICT,
)
from shared.llm.backends import CloudflareWorkersAIBackend
from shared.llm.translator import TranslationService

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

SAMPLES = [
    pytest.param(
        "沪指跌破3800点，光纤板块多只个股跌停",
        "8月1日，上证指数下跌1.2%报3795.36点，光纤概念股集体走弱，"
        "中际旭创(300308)跌停，成交额达45.6亿元。",
        ["3800", "300308"],
        id="chinese-market",
    ),
    pytest.param(
        "贵州茅台上半年净利润同比增长12.5%",
        "贵州茅台(600519)披露2026年半年报，实现营业收入880.3亿元，"
        "归母净利润430.2亿元，同比增长12.5%。",
        ["600519", "12.5"],
        id="chinese-financial-figures",
    ),
    pytest.param(
        "Nvidia beats earnings estimates on AI demand",
        "Nvidia (NVDA) reported quarterly revenue of $62.1 billion, up 28% "
        "year over year, and raised its outlook for the October quarter.",
        ["NVDA", "28"],
        id="global-english",
    ),
]


@pytest.fixture(scope="module")
def service() -> TranslationService:
    backend = CloudflareWorkersAIBackend(
        account_id=CLOUDFLARE_ACCOUNT_ID,
        api_token=CLOUDFLARE_API_TOKEN,
        model=CLOUDFLARE_MODEL,
        base_url=CLOUDFLARE_AI_BASE_URL,
        timeout=CLOUDFLARE_TRANSLATION_TIMEOUT,
    )
    return TranslationService(
        backend=backend,
        enabled=True,
        prompt_dir=PROMPT_DIR,
        num_predict=TRANSLATION_NUM_PREDICT,
    )


@pytest.mark.parametrize(("title", "content", "must_keep"), SAMPLES)
def test_cloudflare_translation_contract(service, title, content, must_keep):
    result = service.translate_article(title, content)

    assert re.search(r"[가-힣]", result.title), result.title
    assert re.search(r"[가-힣]", result.content), result.content
    # 완결된 단신이어야 하며 말줄임으로 끊기지 않는다.
    assert not result.content.rstrip().endswith(("...", "…"))
    assert len(result.content) <= 190, len(result.content)

    combined = f"{result.title} {result.content}"
    missing = [token for token in must_keep if token not in combined]
    assert not missing, f"숫자·종목코드가 유실됨: {missing} / {combined}"
    assert isinstance(result.mentioned_stocks, list)
