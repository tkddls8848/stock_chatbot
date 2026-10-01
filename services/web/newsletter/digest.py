"""그날의 편지 한 통. 공개 산출물(`market.json`·`news.json`)만 읽는다.

구독자 모두에게 같은 내용이 가므로 한 번만 고르고(`build_digest`), 받는 사람마다 다른 것은
서명한 해지 링크뿐이라 그것만 끼워 글로 옮긴다(`render`). LLM을 부르지 않는다 — 시장 요약과
보고서는 봇이 이미 써서 공개해 둔 문장이고, 여기서는 고르고 묶기만 한다. 공개 화면에
없는 것(관심종목·자산·리서치)은 넣지 않는다.

자료가 비었거나 오래됐으면 `None`이다. 어제와 같은 편지를 오늘 또 보내는 것보다
하루 쉬는 편이 낫다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from services.web.core.clock import ensure_jst
from services.web.search import MARKETS

_DISCLAIMER = "참고 정보이며 투자 권유가 아닙니다. 결정과 책임은 본인에게 있습니다."


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _stamp(value: Any) -> datetime | None:
    try:
        return ensure_jst(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _sections(public_dir: Path, moment: datetime, *, window_hours: int,
              max_market_age_hours: int, headlines: int) -> list[dict[str, Any]]:
    market = _read(public_dir / "market.json")
    generated = _stamp(market.get("generated_at"))
    markets = market.get("markets") if generated and moment - generated <= timedelta(hours=max_market_age_hours) else {}
    since = moment - timedelta(hours=window_hours)
    documents = [d for d in _read(public_dir / "news.json").get("documents", []) if isinstance(d, dict)]

    sections = []
    for code, name in MARKETS.items():
        row = (markets or {}).get(code)
        daily = sorted((p for p in (row or {}).get("daily", []) if isinstance(p, dict)),
                       key=lambda p: str(p.get("date", "")))
        last = daily[-1] if daily else None
        reports = [d for d in documents if d.get("kind") == "report" and d.get("market") == code
                   and (_stamp(d.get("published_at")) or since) > since]
        news = [d for d in documents if d.get("kind") == "news" and d.get("market") == code
                and str(d.get("date", "")) >= since.date().isoformat()][:headlines]
        if not (last or reports or news):
            continue
        sections.append({
            "code": code, "name": name,
            "tone": float(last["avg_sentiment"]) if last and last.get("avg_sentiment") is not None else None,
            "tone_date": str(last.get("date", "")) if last else "",
            "summary": str(last.get("summary") or "") if last else "",
            # news.json은 최신순이다. 하루에 두 편 나왔으면 마지막 판단만 싣는다.
            "report": reports[0] if reports else None,
            "news": news,
        })
    return sections


def build_digest(public_dir: Path, moment: datetime, *, window_hours: int, max_market_age_hours: int,
                 headlines: int) -> dict[str, Any] | None:
    sections = _sections(public_dir, moment, window_hours=window_hours,
                         max_market_age_hours=max_market_age_hours, headlines=headlines)
    if not sections:
        return None
    title = f"{moment.month}월 {moment.day}일 시장 요약"
    return {"subject": f"[눈치] {title}", "title": title, "sections": sections}


def render(digest: dict[str, Any], *, site: str, unsubscribe_url: str) -> tuple[str, str]:
    """(본문 글, 본문 HTML). 해지 링크는 받는 사람마다 다르다."""
    return (_text(digest["title"], digest["sections"], site, unsubscribe_url),
            _html(digest["title"], digest["sections"], site, unsubscribe_url))


def _tone(value: float | None) -> str:
    return "" if value is None else f"논조 {value:+.2f}"


def _text(title: str, sections: list[dict[str, Any]], site: str, unsubscribe: str) -> str:
    lines = [f"눈치 · {title}", ""]
    for s in sections:
        lines.append(f"■ {s['name']}  {_tone(s['tone'])}".rstrip())
        if s["summary"]:
            lines.append(s["summary"])
        if s["report"]:
            lines += ["", f"[{s['report'].get('title', '')}]", str(s["report"].get("text", ""))]
        for item in s["news"]:
            lines.append(f"- {item.get('title', '')} ({item.get('source', '')})"
                         + (f"\n  {item['url']}" if item.get("url") else ""))
        lines.append("")
    lines += [f"더 보기: {site}", f"구독 해지: {unsubscribe}", _DISCLAIMER]
    return "\n".join(lines)


def _html(title: str, sections: list[dict[str, Any]], site: str, unsubscribe: str) -> str:
    # 메일 프로그램은 <style>과 외부 글꼴을 버리는 경우가 많아 인라인 스타일만 쓴다.
    # 부호 색은 화면과 같다 — 빨강이 긍정, 파랑이 부정.
    def tone(value: float | None) -> str:
        if value is None:
            return ""
        color = "#b42331" if value > 0 else "#1f57b0" if value < 0 else "#4c5a6b"
        return f" <span style='color:{color};font-weight:700'>{escape(_tone(value))}</span>"

    body = []
    for s in sections:
        part = [f"<h2 style='font-size:17px;margin:24px 0 6px'>{escape(s['name'])}{tone(s['tone'])}</h2>"]
        if s["summary"]:
            part.append(f"<p style='margin:0 0 8px;color:#4c5a6b'>{escape(s['summary'])}</p>")
        if s["report"]:
            paragraphs = "".join(f"<p style='margin:0 0 8px'>{escape(p)}</p>"
                                 for p in str(s["report"].get("text", "")).split("\n") if p.strip())
            part.append(f"<p style='margin:8px 0 4px;font-weight:700'>{escape(str(s['report'].get('title', '')))}</p>"
                        + paragraphs)
        if s["news"]:
            items = []
            for item in s["news"]:
                label = escape(str(item.get("title", "")))
                url = str(item.get("url") or "")
                if url.startswith(("https://", "http://")):
                    label = f"<a href='{escape(url, quote=True)}' style='color:#16324f'>{label}</a>"
                items.append(f"<li style='margin:0 0 4px'>{label} <span style='color:#8a857b'>"
                             f"{escape(str(item.get('source', '')))}</span></li>")
            part.append("<ul style='margin:6px 0 0;padding-left:18px'>" + "".join(items) + "</ul>")
        body.append("".join(part))
    return (
        "<!doctype html><html lang='ko'><body style='margin:0;background:#f5f2ea'>"
        "<div style='max-width:640px;margin:0 auto;padding:24px 16px;background:#fff;color:#0d1b2a;"
        "font-family:-apple-system,\"Apple SD Gothic Neo\",\"Malgun Gothic\",sans-serif;font-size:15px;line-height:1.7'>"
        f"<p style='margin:0;color:#75551b;font-weight:700'>눈치</p><h1 style='font-size:21px;margin:4px 0 0'>{escape(title)}</h1>"
        + "".join(body)
        + f"<hr style='border:0;border-top:1px solid #d3d9e1;margin:28px 0 12px'>"
        f"<p style='font-size:13px;color:#4c5a6b;margin:0 0 4px'><a href='{escape(site, quote=True)}' style='color:#16324f'>눈치에서 더 보기</a> · "
        f"<a href='{escape(unsubscribe, quote=True)}' style='color:#16324f'>뉴스레터 끄기</a></p>"
        f"<p style='font-size:13px;color:#4c5a6b;margin:0'>{escape(_DISCLAIMER)}</p>"
        "</div></body></html>"
    )
