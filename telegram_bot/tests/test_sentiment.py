import json

import pytest

from telegram_bot.llm.translator import TranslationError, TranslationService
from telegram_bot.news.utils import normalize_stock_code


def test_parse_translation_reads_current_schema():
    result = TranslationService._parse_translation(
        json.dumps(
            {
                "title": "제목",
                "content": "본문",
                "mentioned_stocks": ["600519"],
                "sentiment": 0.6,
                "impact": "high",
            },
            ensure_ascii=False,
        )
    )

    assert result.title == "제목"
    assert result.mentioned_stocks == ["600519"]
    assert result.sentiment == 0.6
    assert result.impact == "high"


@pytest.mark.parametrize(
    "field,value",
    [("sentiment", "긍정"), ("sentiment", 2), ("impact", "urgent")],
)
def test_parse_translation_rejects_invalid_current_schema(field, value):
    payload = {
        "title": "제목",
        "content": "본문",
        "mentioned_stocks": [],
        "sentiment": 0,
        "impact": "low",
    }
    payload[field] = value

    with pytest.raises(ValueError):
        TranslationService._parse_translation(json.dumps(payload, ensure_ascii=False))


def test_translation_wraps_backend_error():
    class Backend:
        def generate(self, **kwargs):
            raise RuntimeError("failed")

    service = object.__new__(TranslationService)
    service._enabled = True
    service._backend = Backend()
    service._num_predict = 512
    service._temperature = 0.1
    service._prompt = "prompt"

    with pytest.raises(TranslationError, match="failed"):
        service.translate_article("title", "content")


def test_normalize_stock_code():
    assert normalize_stock_code("700") == "00700"
    assert normalize_stock_code("600519") == "600519"
    assert normalize_stock_code("SZ000665") == "000665"
    assert normalize_stock_code("") == ""
