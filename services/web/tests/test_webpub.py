import json
from html.parser import HTMLParser

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
    for path in ("/", "/forecast", "/search", "/research", "/about", "/portfolio", "/terms",
                 "/robots.txt"):
        body = client.get(path).text
        assert "폴리마켓" not in body
        assert "polymarket" not in body.lower(), path
        assert "베팅" not in body and "배팅" not in body, path
    assert client.get("/polymarket").status_code == 404
    assert client.get("/api/polymarket/summary").status_code == 404


# ── 공유·접근성 ──────────────────────────────────────────────────────────────
# 모든 공개 화면과 잠긴 개인 화면의 껍데기가 대상이다. 화면을 하나 더 만들면
# 여기 목록에 넣는다 — 빠진 화면은 검사되지 않는다.
SCREENS = ("/", "/search", "/forecast", "/research", "/portfolio", "/about", "/terms")


def test_terms_screen_states_the_four_things_it_exists_for():
    """이용 조건·투자 권유 아님·자료의 성격·개인정보 처리가 한 화면에 있다."""
    client = TestClient(server.build_app())
    page = client.get("/terms")

    assert page.status_code == 200
    body = page.text
    assert "이용 조건" in body
    assert "투자 권유" in body and "투자 자문이" in body
    # 자료의 성격 셋을 모두 밝힌다.
    assert "뉴스 집계" in body
    assert "기계가 쓴 요약" in body
    assert "집단 예측 컨센서스" in body
    # 모델이 쓴 문장을 사실로 다루지 않는다는 경고.
    assert "지어내기도" in body
    # 개인정보: 공개 화면은 받지 않고, 쿠키는 잠금용 하나, 접속 기록은 운영 목적.
    assert "공개 화면은 개인정보를 받지 않습니다" in body
    assert "쿠키는 하나뿐입니다" in body
    assert "접속 기록" in body and "서버 운영 목적" in body
    # 운영자 연락처 상수가 없다. 없는 창구를 지어내지 않는다.
    assert "문의" not in body
    assert "@" not in server.TERMS_HTML.split("<body>", 1)[1]


def test_terms_is_reachable_from_every_footer_and_absent_from_the_top_menu():
    """매번 읽는 화면이 아니라 필요할 때 찾는 화면이다."""
    from services.web.pages.shell import _NAV_LINKS

    assert all(href != "/terms" for href, _ in _NAV_LINKS)

    client = TestClient(server.build_app())
    for path in SCREENS:
        body = client.get(path).text
        assert body.count("href='/terms'") == 1, path
        # 꼬리말 안에 있어야 한다 — 본문에 흩어 놓으면 화면마다 자리가 달라진다.
        assert "href='/terms'" in body.split("<footer", 1)[1], path


def test_every_screen_carries_a_description_and_open_graph_tags():
    """카카오톡·슬랙이 붙이는 미리보기 카드가 빈 채로 나가지 않게 한다."""
    import re

    client = TestClient(server.build_app())
    seen_descriptions = set()
    for path in SCREENS:
        head = client.get(path).text.split("</head>", 1)[0]

        description = re.search(r"<meta name='description' content='([^']+)'>", head)
        assert description, path
        assert len(description.group(1)) >= 40, path
        seen_descriptions.add(description.group(1))

        properties = dict(re.findall(r"<meta property='(og:[^']+)' content='([^']*)'>", head))
        assert properties["og:type"] == "website", path
        assert properties["og:locale"] == "ko_KR", path
        assert properties["og:site_name"] == "눈치", path
        assert properties["og:description"] == description.group(1), path
        assert properties["og:title"].endswith(" · 눈치"), path
        assert properties["og:url"] == "https://nunchi.live" + path, path
        # 이미지 파일이 없다. 없는 주소를 적으면 미리보기가 깨진 그림 자리를 만든다.
        assert "og:image" not in properties, path

    # 화면마다 다른 설명을 쓴다 — 같은 문장을 돌려 쓰면 미리보기가 전부 같아진다.
    assert len(seen_descriptions) == len(SCREENS)


def test_body_text_colors_clear_the_contrast_floor():
    """본문 글자색은 바탕 그라데이션이 가장 짙어지는 지점에서도 4.5:1을 넘긴다.

    바탕이 단색이 아니라, 옅은 쪽(`#f5f2ea`)만 재면 아래쪽 꼬리말이 통과한
    것처럼 보인다. 실제로 `--faint`·`--neg`가 짙은 쪽에서 4.33·4.49였다.
    """
    import re

    from services.web.pages.shell import _STYLE

    tokens = dict(re.findall(r"--([a-z0-9-]+):(#[0-9a-f]{6})", _STYLE))
    darkest = re.search(r"linear-gradient\(180deg,#[0-9a-f]{6},#[0-9a-f]{6} 40%,(#[0-9a-f]{6})\)",
                        _STYLE).group(1)

    def luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    background = luminance(darkest)
    for name in ("ink", "ink-soft", "mut", "faint", "gold", "gold-deep", "gold-tint",
                 "acc", "pos", "neg", "ok", "warnc"):
        text = luminance(tokens[name])
        high, low = max(text, background), min(text, background)
        assert (high + 0.05) / (low + 0.05) >= 4.5, f"--{name} {tokens[name]} on {darkest}"


class _ScreenParser(HTMLParser):
    """화면 HTML에서 이름이 필요한 자리를 모은다.

    `<script>`·`<style>` 안은 파서가 CDATA로 건너뛰므로, 브라우저가 나중에
    그리는 조각은 여기 잡히지 않는다. 이 검사가 지키는 것은 **기동할 때 조립되는
    정적 문자열**이고, 그게 이 저장소의 화면 전부다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.unnamed: list[str] = []
        self.headings = 0
        self.lang = ""
        self.skip_link = False
        self._label_depth = 0
        self._labelled_ids: set[str] = set()
        self._controls: list[tuple[str, dict[str, str], bool]] = []
        self._button: list | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: (value or "") for key, value in attrs}
        if tag == "html":
            self.lang = values.get("lang", "")
        elif tag == "label":
            self._label_depth += 1
            if values.get("for"):
                self._labelled_ids.add(values["for"])
        elif tag in ("input", "select", "textarea"):
            if values.get("type") != "hidden":
                self._controls.append((tag, values, self._label_depth > 0))
        elif tag == "button":
            self._button = [values, ""]
        elif tag == "img":
            if "alt" not in values:
                self.unnamed.append("img src=" + values.get("src", "?"))
        elif tag == "h1":
            self.headings += 1
        elif tag == "a":
            if values.get("class") == "skip" and values.get("href") == "#main":
                self.skip_link = True
        elif tag in ("div", "section") and values.get("tabindex") == "0":
            if not (values.get("aria-label") or values.get("aria-labelledby")):
                self.unnamed.append("scrollable region class=" + values.get("class", "?"))

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self._label_depth = max(0, self._label_depth - 1)
        elif tag == "button" and self._button is not None:
            values, text = self._button
            if not (text.strip() or values.get("aria-label") or values.get("aria-labelledby")):
                self.unnamed.append("button id=" + (values.get("id") or values.get("class", "?")))
            self._button = None

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button[1] += data

    def finish(self) -> "_ScreenParser":
        for tag, values, inside_label in self._controls:
            named = (
                inside_label
                or values.get("aria-label")
                or values.get("aria-labelledby")
                or values.get("id") in self._labelled_ids
            )
            if not named:
                self.unnamed.append(tag + " name=" + values.get("name", values.get("id", "?")))
        return self


def _parse(html: str) -> _ScreenParser:
    parser = _ScreenParser()
    parser.feed(html)
    return parser.finish()


def test_every_screen_names_its_controls_and_offers_a_way_past_the_menu():
    """라벨 없는 입력칸·이름 없는 버튼·건너뛰기 링크 누락을 정적으로 막는다.

    `placeholder`는 라벨이 아니다 — 값을 적는 순간 사라지고, 화면 낭독기가
    읽어 준다는 보장도 없다. 넘치는 표를 감싼 스크롤 칸도 이름이 있어야
    키보드로 들어갔을 때 무엇을 미는 칸인지 알 수 있다.
    """
    client = TestClient(server.build_app())
    for path in SCREENS:
        screen = _parse(client.get(path).text)
        assert screen.unnamed == [], (path, screen.unnamed)
        assert screen.lang == "ko", path
        assert screen.skip_link, path
        # 제목이 둘이면 화면 낭독기의 목차가 갈라진다.
        assert screen.headings == 1, path


def test_keyboard_focus_stays_visible():
    """포커스 표시를 지우지 않는다. 지우면 키보드로 도는 사람이 길을 잃는다."""
    from services.web.pages.shell import _STYLE

    assert "outline:2px solid var(--acc);outline-offset:2px}" in _STYLE
    for selector in ("a:focus-visible", "button:focus-visible", "input:focus-visible",
                     "select:focus-visible", "textarea:focus-visible", "[tabindex]:focus-visible"):
        assert selector in _STYLE, selector
    assert "outline:none" not in _STYLE.replace("main#main:focus{outline:none}", "")
