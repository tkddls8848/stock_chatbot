import json

from fastapi.testclient import TestClient

from services.web import server


# 봇(`services/telegram_bot/publish.py`)이 storage/public/에 쓰는 형식이다. 웹은 봇을
# import하지 않으므로 계약 파일을 직접 써서 읽는 쪽을 검사한다. 형식을 바꾸면
# 봇의 test_publish.py와 여기를 같은 커밋에서 고친다.
def _write(root, name, payload):
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_publish_market_and_serve_it(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_DIR", tmp_path)
    _write(tmp_path, "market.json", {
        "generated_at": "2026-09-24T07:40:00+09:00", "lookback_days": 7,
        "markets": {"KR": {"avg_sentiment": 0.2, "count": 12, "daily": []},
                    "JP": {"avg_sentiment": -0.1, "count": 15, "daily": []}},
    })
    (tmp_path / "market_chart.png").write_bytes(b"png-bytes")

    client = TestClient(server.build_app())
    payload = client.get("/api/market").json()
    assert payload["markets"]["KR"]["count"] == 12
    assert payload["markets"]["JP"]["count"] == 15
    assert client.get("/market_chart.png").content == b"png-bytes"
    assert client.get("/").status_code == 200


def test_market_page_includes_japan_before_its_data_is_ready():
    assert "const MARKETS=['CN','HK','US','KR','JP']" in server.INDEX_HTML
    assert "자료 수집·분석 대기" in server.INDEX_HTML
    for body in (server.INDEX_HTML, server.ABOUT_HTML):
        assert "일본" in body


def test_publish_research_preserves_full_result_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_DIR", tmp_path)

    result = {"summary": "시장 요약", "risks": ["변동성"]}
    _write(tmp_path, "research.json", {"generated_at": "2026-09-24T08:20:00+09:00", "sight": "반도체",
                                       "last_result": result, "history": [{"summary": "이전 결과"}]})

    payload = TestClient(server.build_app()).get("/api/research").json()
    assert payload["sight"] == "반도체"
    assert payload["last_result"] == result
    assert payload["history"] == [{"summary": "이전 결과"}]


def test_public_pages_share_one_shell(tmp_path, monkeypatch):
    """세 화면이 같은 헤더·푸터를 쓰고 현재 위치를 표시한다."""
    monkeypatch.setattr(server, "PUBLIC_DIR", tmp_path)
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
    monkeypatch.setattr(server, "PUBLIC_DIR", tmp_path / "missing")
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


def test_pages_label_the_clock_as_korean_time():
    """읽는 사람은 한국에 있다. 값은 같아도 `JST`는 남의 나라 시간으로 읽힌다.

    `UTC +9`도 쓰지 않는다 — 시차 표기는 전문 용어라 화면은 "한국 시간"으로
    적는다. 저장 문자열(`compact_jst_time`)은 그대로 두고 표시 직전에만 바꾼다 —
    큐·로그에 이미 `JST`·`KST`로 적힌 값을 계속 파싱해야 한다.
    """
    for body in (
        server.INDEX_HTML,
        server.ABOUT_HTML,
        server.RESEARCH_HTML,
        server.POLYMARKET_HTML,
        server.SEARCH_HTML,
    ):
        assert "JST" not in body
        assert "UTC +9" not in body
        assert "한국 시간" in body


def test_market_chart_is_revalidated_instead_of_heuristically_cached(tmp_path, monkeypatch):
    """차트 URL은 고정이라 캐시 지시가 없으면 브라우저가 옛 그림을 계속 쓴다."""
    monkeypatch.setattr(server, "PUBLIC_DIR", tmp_path)
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

    monkeypatch.setattr(server, "PUBLIC_DIR", tmp_path)
    client = TestClient(server.build_app())
    assert client.get("/api/forecast/trending").status_code == 503

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

    payload = client.get("/api/forecast/trending").json()
    assert payload["spotlight"][0]["basis_change"] == 0.2
    assert "baseline" not in payload
    assert "previous" not in payload


def test_robots_blocks_the_heavy_api_face_but_not_search_crawlers(tmp_path, monkeypatch):
    """크롤러를 통째로 막지 않는 것은 결정이다.

    막으면 크롤러가 `X-Robots-Tag: noindex`를 읽지 못해 내용 없이 URL만 색인에
    남는다(`infra/server-ops.md` 11절). 그래서 막는 것은 부하를 만드는 `/api/`
    면과, 검색 색인과 다른 UA를 쓰는 AI 수집 봇뿐이다.
    """
    monkeypatch.setattr(server, "PUBLIC_DIR", tmp_path)
    response = TestClient(server.build_app()).get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text

    general = body.split("User-agent: GPTBot")[0]
    assert "User-agent: *" in general
    assert "Disallow: /api/" in general
    # 검색어가 붙은 화면 주소는 조합이 무한하다. 화면 자체는 열어 둔다.
    assert "Disallow: /*?\n" in general
    assert "Disallow: /search\n" not in general and "Disallow: /polymarket\n" not in general
    # 전면 차단은 noindex를 읽을 통로까지 막는다.
    assert "Disallow: /\n" not in general

    for agent in ("GPTBot", "ClaudeBot", "CCBot", "Google-Extended", "Bytespider"):
        assert f"User-agent: {agent}" in body
    assert body.rstrip().endswith("Disallow: /")


def test_robots_and_caddy_block_the_same_agents():
    """권고(robots.txt)와 강제(Caddy)가 갈라지면 한쪽만 막힌 채로 돈다.

    표식(`# BEGIN aibots` ~ `# END aibots`)도 함께 본다.
    `infra/scripts/apply-caddy-bots.sh`가 그 사이를 잘라 호스트 설정에 옮기므로,
    표식이 사라지거나 matcher가 그 밖으로 나가면 스크립트가 빈 블록을 넣는다 —
    차단이 사라진 채로 reload까지 성공해 버린다.
    """
    from pathlib import Path

    from services.web.pages.robots import AI_AGENTS

    lines = (
        Path(__file__).resolve().parents[3] / "infra" / "Caddyfile.example"
    ).read_text(encoding="utf-8").splitlines()

    begins = [i for i, line in enumerate(lines) if "# BEGIN aibots" in line]
    ends = [i for i, line in enumerate(lines) if "# END aibots" in line]
    assert len(begins) == 1 and len(ends) == 1 and begins[0] < ends[0]

    block = lines[begins[0] : ends[0] + 1]
    matcher = [line for line in block if "@aibots" in line and "header_regexp" in line]
    assert len(matcher) == 1
    assert any(line.strip() == "abort @aibots" for line in block)
    for agent in AI_AGENTS:
        assert agent in matcher[0]


def test_caddy_bot_block_spares_search_crawlers_browsers_and_shorts():
    """차단 정규식이 넓어지면서 지켜야 할 UA까지 끊지 않는가.

    검색 크롤러가 막히면 noindex를 못 읽는다. 쇼츠는 공개 주소의 API를 기본
    requests UA로 읽는다 — 막으면 쇼츠가 선다.
    """
    import re
    from pathlib import Path

    line = next(
        line for line in (Path(__file__).resolve().parents[3] / "infra" / "Caddyfile.example")
        .read_text(encoding="utf-8").splitlines()
        if "@aibots header_regexp" in line
    )
    pattern = re.compile(line.split('"', 2)[1].removeprefix("(?i)"), re.IGNORECASE)

    for spared in (
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "Mozilla/5.0 (compatible; Yeti/1.1; +http://naver.me/spd)",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
        "python-requests/2.32.3",
    ):
        assert not pattern.search(spared), spared
    for blocked in ("Mozilla/5.0 (compatible; GoogleOther)", "Scrapy/2.11 (+https://scrapy.org)"):
        assert pattern.search(blocked), blocked


def test_forecast_screen_never_names_the_source_service():
    # 한국에서 공식적으로 접근이 막힌 서비스라 화면·주소·외부 링크에 이름을 드러내지 않는다.
    client = TestClient(server.build_app())
    for path in ("/", "/forecast", "/search", "/research", "/about", "/robots.txt"):
        body = client.get(path).text
        assert "폴리마켓" not in body
        assert "polymarket" not in body.lower(), path
        assert "베팅" not in body and "배팅" not in body, path
    assert client.get("/polymarket").status_code == 404
    assert client.get("/api/polymarket/summary").status_code == 404
