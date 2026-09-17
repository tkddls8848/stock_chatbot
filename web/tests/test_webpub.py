from fastapi.testclient import TestClient

from web import export, server


def test_publish_market_and_serve_it(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "WEBPUB_DIR", tmp_path)
    monkeypatch.setattr(export, "MARKET_JSON", tmp_path / "market.json")
    monkeypatch.setattr(export, "MARKET_CHART", tmp_path / "market_chart.png")
    monkeypatch.setattr(export, "META_JSON", tmp_path / "meta.json")
    monkeypatch.setattr(server, "WEBPUB_DIR", tmp_path)

    export.publish_market(
        b"png-bytes",
        {"KR": {"avg_sentiment": 0.2, "count": 12, "daily": []}},
        7,
    )

    client = TestClient(server.build_app())
    payload = client.get("/api/market").json()
    assert payload["markets"]["KR"]["count"] == 12
    assert client.get("/market_chart.png").content == b"png-bytes"
    assert client.get("/").status_code == 200


def test_publish_research_preserves_full_result_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "WEBPUB_DIR", tmp_path)
    monkeypatch.setattr(export, "RESEARCH_JSON", tmp_path / "research.json")
    monkeypatch.setattr(export, "META_JSON", tmp_path / "meta.json")
    monkeypatch.setattr(server, "WEBPUB_DIR", tmp_path)

    result = {"summary": "시장 요약", "risks": ["변동성"]}
    export.publish_research("반도체", result, [{"summary": "이전 결과"}])

    payload = TestClient(server.build_app()).get("/api/research").json()
    assert payload["sight"] == "반도체"
    assert payload["last_result"] == result
    assert payload["history"] == [{"summary": "이전 결과"}]


def test_public_pages_share_one_shell(tmp_path, monkeypatch):
    """세 화면이 같은 헤더·푸터를 쓰고 현재 위치를 표시한다."""
    monkeypatch.setattr(server, "WEBPUB_DIR", tmp_path)
    client = TestClient(server.build_app())

    for path in ("/", "/research", "/about"):
        page = client.get(path)
        assert page.status_code == 200
        body = page.text
        # 산출물이 없어도 화면 자체는 그려진다. 값은 브라우저가 /api/*로 채운다.
        assert "nunchi" in body
        for link in ("/", "/research", "/about"):
            assert "href='" + link + "'" in body
        assert "href='" + path + "' aria-current='page'" in body


def test_pages_are_built_once_and_do_not_touch_the_filesystem(tmp_path, monkeypatch):
    """페이지는 정적 문자열이다. 요청마다 다시 조립하거나 산출물을 읽지 않는다."""
    monkeypatch.setattr(server, "WEBPUB_DIR", tmp_path / "missing")
    client = TestClient(server.build_app())

    first = client.get("/about").text
    assert first == client.get("/about").text
    assert first == server.ABOUT_HTML


def test_research_metadata_uses_the_same_section_card_pattern():
    """연구 결과 메타데이터도 요약·리스크와 같은 섹션형 카드로 표시한다."""
    body = server.RESEARCH_HTML

    assert "</span>연구 결과</div>" in body
    assert "<dl class='brief research-meta'>" in body
    assert "<div class='statstrip'>" not in body


def test_pages_label_the_clock_as_an_offset_not_japan_standard_time():
    """읽는 사람은 한국에 있다. 값은 같아도 `JST`는 남의 나라 시간으로 읽힌다.

    저장 문자열(`compact_jst_time`)은 그대로 두고 표시 직전에만 바꾼다 — 큐·로그에
    이미 `JST`·`KST`로 적힌 값을 계속 파싱해야 한다.
    """
    for body in (
        server.INDEX_HTML,
        server.ABOUT_HTML,
        server.RESEARCH_HTML,
        server.POLYMARKET_HTML,
    ):
        assert "JST" not in body
        assert "UTC +9" in body


def test_market_chart_is_revalidated_instead_of_heuristically_cached(tmp_path, monkeypatch):
    """차트 URL은 고정이라 캐시 지시가 없으면 브라우저가 옛 그림을 계속 쓴다."""
    monkeypatch.setattr(server, "WEBPUB_DIR", tmp_path)
    chart = tmp_path / "market_chart.png"
    chart.write_bytes(b"png-bytes")

    client = TestClient(server.build_app())
    first = client.get("/market_chart.png")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-cache"

    etag = first.headers["etag"]
    assert client.get("/market_chart.png", headers={"if-none-match": etag}).status_code == 304

    # 새로 구운 차트는 같은 URL로도 곧바로 나가야 한다.
    chart.write_bytes(b"new-png-bytes")
    fresh = client.get("/market_chart.png", headers={"if-none-match": etag})
    assert fresh.status_code == 200
    assert fresh.content == b"new-png-bytes"


def test_trending_route_hides_the_next_cycle_state(tmp_path, monkeypatch):
    """화면에 나가는 것은 조명 결과뿐이다.

    `baseline`·`previous`는 다음 주기가 이동을 계산할 상태이고 후보 수백 건짜리다.
    줄글 브리프의 `previous`와 같은 이유로 잘라낸다.
    """
    import json

    monkeypatch.setattr(server, "WEBPUB_DIR", tmp_path)
    client = TestClient(server.build_app())
    assert client.get("/api/polymarket/trending").status_code == 503

    target = tmp_path / "polymarket" / "trending.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "generation_id": "g1",
                "state": "ok",
                "basis": "day",
                "spotlight": [{"id": "1", "title": "event 1", "basis_change": 0.2}],
                "baseline": {"events": {"1": {"p": 0.3}}},
                "previous": {"events": {"1": {"p": 0.5}}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = client.get("/api/polymarket/trending").json()
    assert payload["spotlight"][0]["basis_change"] == 0.2
    assert "baseline" not in payload
    assert "previous" not in payload


def test_robots_blocks_the_heavy_api_face_but_not_search_crawlers(tmp_path, monkeypatch):
    """크롤러를 통째로 막지 않는 것은 결정이다.

    막으면 크롤러가 `X-Robots-Tag: noindex`를 읽지 못해 내용 없이 URL만 색인에
    남는다(`infra/server-ops.md` 11절). 그래서 막는 것은 부하를 만드는 `/api/`
    면과, 검색 색인과 다른 UA를 쓰는 AI 수집 봇뿐이다.
    """
    monkeypatch.setattr(server, "WEBPUB_DIR", tmp_path)
    response = TestClient(server.build_app()).get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text

    general = body.split("User-agent: GPTBot")[0]
    assert "User-agent: *" in general
    assert "Disallow: /api/" in general
    # 전면 차단은 noindex를 읽을 통로까지 막는다.
    assert "Disallow: /\n" not in general

    for agent in ("GPTBot", "ClaudeBot", "CCBot", "Google-Extended", "Bytespider"):
        assert f"User-agent: {agent}" in body
    assert body.rstrip().endswith("Disallow: /")


def test_robots_and_caddy_block_the_same_agents():
    """권고(robots.txt)와 강제(Caddy)가 갈라지면 한쪽만 막힌 채로 돈다."""
    from pathlib import Path

    from web.pages.robots import AI_AGENTS

    caddyfile = (
        Path(__file__).resolve().parents[2] / "infra" / "Caddyfile.example"
    ).read_text(encoding="utf-8")
    matcher = [line for line in caddyfile.splitlines() if "@aibots" in line and "header_regexp" in line]
    assert len(matcher) == 1
    for agent in AI_AGENTS:
        assert agent in matcher[0]
