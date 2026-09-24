"""`/robots.txt`.

화면이 아니지만 여기 둔다 — 기동 시 한 번 조립되는 정적 문자열이라는 점이
`pages/`의 다른 파일과 같고, 라우트 파일(`server.py`)에는 내용 문자열을 두지
않는다.

**검색 크롤러를 통째로 막지 않는다.** 막으면 크롤러가 `X-Robots-Tag: noindex`를
읽지 못해 내용 없이 URL만 색인에 남는다(`infra/server-ops.md` 11절). 그래서
막는 것은 둘뿐이다.

1. `/api/` — 부하의 실체다. `/api/polymarket/events`는 필터·정렬·페이지 조합이
   사실상 무한한 URL 공간이고, `/api/polymarket/events/{id}`는 event 22,000건이
   각각 detail shard를 seek한다. 크롤러가 이 둘을 훑으면 1 GiB 인스턴스에서
   순회 one-shot·봇과 CPU를 다툰다. 화면이 읽는 값이므로 사람이 보는 데는
   지장이 없다 — 브라우저는 robots.txt를 읽지 않는다.
2. AI 학습·수집 봇 — 검색 색인과 **다른 UA**라 통째로 막아도 noindex를 읽는
   크롤러(Googlebot 등)에는 영향이 없다. `Google-Extended`가 정확히 그 분리를
   위해 존재하는 UA다.

3. 검색어가 붙은 화면 주소(`/search?q=…`, `/polymarket?q=…&category=…`) — 조합이
   사실상 무한한 URL 공간이라 크롤러가 끝없이 돈다. 화면 자체(`/search`,
   `/polymarket`)는 열어 두고 `?`가 붙은 변형만 막는다(`Disallow: /*?`).

**일반 HTTP 라이브러리(`python-requests`, `curl`)는 막지 않는다.** 쇼츠가 공개
주소(`https://nunchi.live`)의 API를 기본 `requests` UA로 읽는다 — 막으면 쇼츠가 선다.

robots.txt는 권고일 뿐이라 무시하는 봇에는 효과가 없다. 실제로 끊는 것은 앞단
Caddy의 User-Agent matcher이고(`infra/Caddyfile.example`), 여기 목록과 그쪽
목록은 **같이 고친다** — 갈라지면 한쪽만 막힌 채로 돈다.
"""

from __future__ import annotations

# Caddy의 @aibots 정규식과 같은 목록이다. 한쪽만 고치지 않는다.
AI_AGENTS = (
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "Claude-Web",
    "anthropic-ai",
    "CCBot",
    "Google-Extended",
    "PerplexityBot",
    "Bytespider",
    "Amazonbot",
    "meta-externalagent",
    "FacebookBot",
    "Applebot-Extended",
    "cohere-ai",
    "Diffbot",
    "ImagesiftBot",
    "Omgilibot",
    "YouBot",
    "Timpibot",
    "AhrefsBot",
    "SemrushBot",
    "MJ12bot",
    "DotBot",
    "DataForSeoBot",
    # 2026-09-24 추가: 검색 색인과 따로 도는 AI·수집 UA.
    "GoogleOther",
    "Google-CloudVertexBot",
    "Meta-ExternalFetcher",
    "Perplexity-User",
    "MistralAI-User",
    "DuckAssistBot",
    "cohere-training-data-crawler",
    "Webzio-Extended",
    "PanguBot",
    "Kangaroo Bot",
    "PetalBot",
    "BLEXBot",
    "img2dataset",
    "Scrapy",
)

ROBOTS_TXT = (
    "# nunchi.live — 읽기 전용 공개 웹\n"
    "#\n"
    "# 검색 크롤러는 막지 않는다. 막으면 X-Robots-Tag: noindex를 읽지 못해\n"
    "# 내용 없이 URL만 색인에 남는다. 막는 것은 무거운 API 면, 검색어가 붙은 화면\n"
    "# 주소(조합이 무한하다), AI 학습·수집 봇이다.\n"
    "\n"
    "User-agent: *\n"
    "Disallow: /api/\n"
    "Disallow: /*?\n"
    "Crawl-delay: 10\n"
    "\n"
    "# AI 학습·수집 봇. 검색 색인과 다른 UA라 noindex 전달에 영향을 주지 않는다.\n"
    + "".join(f"User-agent: {agent}\n" for agent in AI_AGENTS)
    + "Disallow: /\n"
)
