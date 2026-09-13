"""리서치 evidence를 news_items의 id 참조로 주고받는지 검증한다.

모델이 원문 URL을 그대로 받아 적으면 출력 예산의 3분의 1이 base64에 쓰여
분석 JSON이 상한에서 잘린다(2026-08-24 운영 장애). 모델은 어느 기사인지만
가리키고 URL은 서버가 붙인다 — 뉴스 번역이 이미 쓰는 방식과 같다.
"""
import json


from telegram_bot.llm.market_view import MarketViewAnalyzer


def _news_items() -> list[dict]:
    return [
        {
            "id": "무시되는 예전 형식",
            "source": "gnews_us",
            "market": "US",
            "title": "Fed signals slower cuts",
            "content": "본문",
            "published_at": "2026-08-24 09:15",
            "url": "https://news.google.com/rss/articles/" + "A" * 240,
        },
        {
            "source": "cls",
            "market": "CN",
            "title": "반도체 보조금 확대",
            "content": "본문",
            "published_at": "2026-08-24 10:20",
            "url": "https://www.cls.cn/detail/12345",
        },
    ]


class _CapturingBackend:
    """user_prompt를 그대로 붙잡아 두는 가짜 백엔드."""

    model = "test-model"

    def __init__(self, response: str):
        self._response = response
        self.user_prompt = ""

    def generate(self, *, system_prompt, user_prompt, max_tokens, temperature, timeout):
        self.user_prompt = user_prompt
        return self._response


def _analyzer(tmp_path, backend=None) -> MarketViewAnalyzer:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("test prompt", encoding="utf-8")
    return MarketViewAnalyzer(
        backend=backend,
        timeout=10,
        num_predict=512,
        prompt_file=prompt_file,
    )


def _parse(tmp_path, payload: dict, news_items=None) -> dict:
    return _analyzer(tmp_path)._parse_analysis(
        json.dumps(payload, ensure_ascii=False),
        news_items=news_items if news_items is not None else _news_items(),
    )


def _action(evidence) -> dict:
    return {
        "ticker": "US:NASDAQ:AAPL",
        "name": "Apple",
        "action": "add",
        "confidence": 0.7,
        "relevance": 0.8,
        "reason": "이유",
        "evidence": evidence,
    }


def test_payload_gives_news_items_ordinal_ids_and_drops_urls(tmp_path):
    """모델에게 보내는 news_items는 순번 id를 갖고 URL을 싣지 않는다."""
    backend = _CapturingBackend(
        json.dumps({"summary": "요약", "actions": [], "risks": [], "view_critique": []})
    )
    analyzer = _analyzer(tmp_path, backend)
    items = _news_items()

    analyzer.analyze(market_view="뷰", watchlist={}, news_items=items)

    sent = json.loads(backend.user_prompt)["news_items"]
    assert [row["id"] for row in sent] == [0, 1]
    assert all("url" not in row for row in sent)
    assert "news.google.com" not in backend.user_prompt
    # 원본은 건드리지 않는다 - 서버가 URL을 되찾아야 한다.
    assert items[0]["url"].startswith("https://news.google.com/")


def test_parse_resolves_action_evidence_id_to_full_article(tmp_path):
    """evidence의 id를 서버가 제목·소스·시각·URL로 되살린다."""
    result = _parse(
        tmp_path,
        {
            "summary": "요약",
            "actions": [_action([{"id": 1}])],
            "risks": [],
            "view_critique": [],
        },
    )

    evidence = result["actions"][0]["evidence"]
    assert evidence == [
        {
            "title": "반도체 보조금 확대",
            "source": "cls",
            "published_at": "2026-08-24 10:20",
            "url": "https://www.cls.cn/detail/12345",
        }
    ]


def test_parse_accepts_string_id(tmp_path):
    """소형 모델이 id를 문자열로 답해도 같은 기사로 해석한다."""
    result = _parse(
        tmp_path,
        {
            "summary": "요약",
            "actions": [_action([{"id": "0"}])],
            "risks": [],
            "view_critique": [],
        },
    )

    assert result["actions"][0]["evidence"][0]["source"] == "gnews_us"


def test_parse_drops_unknown_evidence_id(tmp_path):
    """입력에 없는 id는 조용히 뺀다 - 보조 출력이라 실행을 죽이지 않는다."""
    result = _parse(
        tmp_path,
        {
            "summary": "요약",
            "actions": [_action([{"id": 99}, {"id": 0}, {"title": "지어낸 근거"}])],
            "risks": [],
            "view_critique": [],
        },
    )

    evidence = result["actions"][0]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["title"] == "Fed signals slower cuts"


def test_parse_caps_action_evidence_at_two(tmp_path):
    """프롬프트가 지시한 2건 상한을 표시 직전에 다시 지킨다."""
    result = _parse(
        tmp_path,
        {
            "summary": "요약",
            "actions": [_action([{"id": 0}, {"id": 1}, {"id": 0}])],
            "risks": [],
            "view_critique": [],
        },
    )

    assert len(result["actions"][0]["evidence"]) == 2


def test_parse_resolves_view_critique_evidence_id(tmp_path):
    """반론의 evidence도 같은 id 참조를 쓴다."""
    result = _parse(
        tmp_path,
        {
            "summary": "요약",
            "actions": [],
            "risks": [],
            "view_critique": [{"point": "반론", "severity": 0.6, "evidence": {"id": 1}}],
        },
    )

    evidence = result["view_critique"][0]["evidence"]
    assert evidence["title"] == "반도체 보조금 확대"
    assert evidence["url"] == "https://www.cls.cn/detail/12345"


def test_parse_view_critique_unknown_id_becomes_none(tmp_path):
    result = _parse(
        tmp_path,
        {
            "summary": "요약",
            "actions": [],
            "risks": [],
            "view_critique": [{"point": "반론", "evidence": {"id": 99}}],
        },
    )

    assert result["view_critique"][0]["evidence"] is None
