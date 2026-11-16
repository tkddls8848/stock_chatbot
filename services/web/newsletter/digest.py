"""공통 브리핑과 대체 시장 요약을 편지로 렌더한다."""

from __future__ import annotations

import re

from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from services.web.core.clock import ensure_jst
from services.web.search import MARKETS
from services.web.core.storage import read_json_object

_DISCLAIMER = "참고 정보이며 투자 권유가 아닙니다. 결정과 책임은 본인에게 있습니다."


def _stamp(value: Any) -> datetime | None:
    try:
        return ensure_jst(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _sections(public_dir: Path, moment: datetime, *, window_hours: int,
              max_market_age_hours: int, headlines: int) -> list[dict[str, Any]]:
    market = read_json_object(public_dir / "market.json")
    generated = _stamp(market.get("generated_at"))
    markets = market.get("markets") if generated and timedelta(0) <= moment - generated <= timedelta(hours=max_market_age_hours) else {}
    since = moment - timedelta(hours=window_hours)
    documents = [d for d in read_json_object(public_dir / "news.json").get("documents", []) if isinstance(d, dict)]

    sections = []
    for code, name in MARKETS.items():
        row = (markets or {}).get(code)
        daily = sorted((p for p in (row or {}).get("daily", []) if isinstance(p, dict)),
                       key=lambda p: str(p.get("date", "")))
        last = daily[-1] if daily else None
        reports = [d for d in documents if d.get("kind") == "report" and d.get("market") == code
                   and since <= (_stamp(d.get("published_at")) or since - timedelta(seconds=1)) <= moment]
        news = [d for d in documents if d.get("kind") == "news" and d.get("market") == code
                and since <= (_stamp(d.get("published_at") or d.get("date")) or since - timedelta(seconds=1)) <= moment
                and re.search("[가-힣]", str(d.get("title") or ""))][:min(headlines, 2)]
        if not (last or reports or news):
            continue
        sections.append({
            "code": code, "name": name,
            "tone": float(last["avg_sentiment"]) if last and last.get("avg_sentiment") is not None else None,
            "tone_date": str(last.get("date", "")) if last else "",
            "summary": str(last.get("summary") or "") if last else "",
            "news": news,
        })
    return sections


def build_digest(public_dir: Path, moment: datetime, *, window_hours: int, max_market_age_hours: int,
                 headlines: int, briefing: dict | None = None) -> dict[str, Any] | None:
    moment = ensure_jst(moment)
    sections = _sections(public_dir, moment, window_hours=window_hours,
                         max_market_age_hours=max_market_age_hours, headlines=headlines)
    if not sections and not briefing:
        return None
    title = f"{moment.month}월 {moment.day}일 시장 요약"
    video = read_json_object(public_dir / "shorts" / "ko.json")
    at = _stamp(video.get("published_at") or video.get("date"))
    if (not at or not timedelta(0) <= moment - at <= timedelta(hours=48)
            or not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", str(video.get("video_id") or ""))):
        video = None
    else:
        video = {"title": str(video.get("title") or ""),
                 "url": f"https://www.youtube.com/watch?v={video['video_id']}"}
    title = briefing["subject"] if briefing else title
    return {"subject": f"[눈치] {title}", "title": title, "briefing": briefing,
            "sections": sections, "tones": [{k: s[k] for k in ("code", "name", "tone")}
                                             for s in sections if s["tone"] is not None], "video": video}


def personal_section(watch: dict[str, Any], alerts_sent: list[str]) -> dict[str, Any]:
    """내 자산 점검(`portfolio/watch.py`) 결과에서 편지에 실을 것만 고른다. 금액이 든 `message` 대신
    `brief`를 쓰고, 지난 편지 때 이미 넘어 있던 기준은 다시 싣지 않는다."""
    previous = set(alerts_sent)
    return {
        "alerts": [a["brief"] for a in watch["alerts"] if a["key"] not in previous],
        "holdings": [{"name": h["name"], "held": h["held"], "market_label": h["market_label"],
                      "matches": h["matches"]} for h in watch["holdings"] if h["count"]],
    }


def subject(digest: dict[str, Any], personal: dict[str, Any] | None) -> str:
    alerts = len((personal or {}).get("alerts") or [])
    return digest["subject"] + (f" · 내 자산 알림 {alerts}건" if alerts else "")


def render(digest: dict[str, Any], *, site: str, unsubscribe_url: str,
           personal: dict[str, Any] | None = None) -> tuple[str, str]:
    """(본문 글, 본문 HTML). 해지 링크와 내 자산 소식은 받는 사람마다 다르다."""
    return (_text(digest, site, unsubscribe_url, personal),
            _html(digest, site, unsubscribe_url, personal))


def _tone(value: float | None) -> str:
    return "" if value is None else f"논조 {value:+.2f}"


def _sources(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    """브리핑 항목 아래 원 기사 줄. 같은 주소·제목은 한 번만, 한국어 제목이 없는 기사는 "원문 기사"로 보인다 —
    중국어·일본어 원문 제목을 그대로 실으면 읽는 사람이 무엇인지 알 수 없다(2026-11-16 미리보기)."""
    rows, seen = [], set()
    for source in sources:
        title = str(source.get("title") or "")
        url = str(source.get("url") or "")
        key = url or title
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append({"title": title if re.search("[가-힣]", title) else "원문 기사",
                     "source": str(source.get("source") or ""),
                     "url": url if url.startswith(("https://", "http://")) else ""})
    return rows


def _text(digest: dict, site: str, unsubscribe: str,
          personal: dict[str, Any] | None) -> str:
    lines = [f"눈치 · {digest['title']}", ""]
    if personal is not None:
        lines.append("■ 내 자산 소식")
        lines += [f"! {line}" for line in personal["alerts"]]
        for holding in personal["holdings"]:
            lines.append(f"[{'보유' if holding['held'] else '관심'}] {holding['name']}")
            lines += [f"- {m['title']} ({m['source']})" + (f"\n  {m['url']}" if m["url"] else "")
                      for m in holding["matches"]]
        if not (personal["alerts"] or personal["holdings"]):
            lines.append("새로 넘은 점검 기준도, 내 종목이 나온 기사도 없습니다.")
        lines.append("")
    briefing = digest.get("briefing")
    if briefing:
        lines += [briefing["lead"], ""]
        for item in briefing["items"]:
            lines += [f"■ {item['title']}", item["body"]]
            lines += [f"- {n['title']} ({n['source']})" + (f"\n  {n['url']}" if n["url"] else "")
                      for n in _sources(item["sources"])]
            lines.append("")
        lines += ["■ 오늘 지켜볼 것", *[p["text"] for p in briefing["watch"]], ""]
    for s in ([] if briefing else digest["sections"]):
        lines.append(f"■ {s['name']}  {_tone(s['tone'])}".rstrip())
        if s["summary"]:
            lines.append(s["summary"])
        for item in s["news"]:
            lines.append(f"- {item.get('title', '')} ({item.get('source', '')})"
                         + (f"\n  {item['url']}" if item.get("url") else ""))
        lines.append("")
    lines += ["시장 논조: " + " · ".join(f"{t['name']} {t['tone']:+.2f}" for t in digest["tones"])]
    if digest.get("video"):
        lines += [f"오늘의 영상: {digest['video']['title']} {digest['video']['url']}"]
    lines += [f"더 보기: {site}", f"구독 해지: {unsubscribe}", _DISCLAIMER]
    return "\n".join(lines)


def _html(digest: dict, site: str, unsubscribe: str,
          personal: dict[str, Any] | None) -> str:
    # 메일 프로그램은 <style>과 외부 글꼴을 버리는 경우가 많아 인라인 스타일만 쓴다.
    # 부호 색은 화면과 같다 — 빨강이 긍정, 파랑이 부정.
    def tone(value: float | None) -> str:
        if value is None:
            return ""
        color = "#b42331" if value > 0 else "#1f57b0" if value < 0 else "#4c5a6b"
        return f" <span style='color:{color};font-weight:700'>{escape(_tone(value))}</span>"

    def link(item: dict[str, Any]) -> str:
        label = escape(str(item.get("title", "")))
        url = str(item.get("url") or "")
        if url.startswith(("https://", "http://")):
            label = f"<a href='{escape(url, quote=True)}' style='color:#16324f'>{label}</a>"
        return (f"<li style='margin:0 0 4px'>{label} <span style='color:#8a857b'>"
                f"{escape(str(item.get('source', '')))}</span></li>")

    body = []
    if personal is not None:
        part = ["<h2 style='font-size:17px;margin:24px 0 6px'>내 자산 소식</h2>"]
        part += [f"<p style='margin:0 0 6px;padding-left:10px;border-left:3px solid #8f4511'>{escape(line)}</p>"
                 for line in personal["alerts"]]
        for holding in personal["holdings"]:
            part.append(f"<p style='margin:10px 0 2px;font-weight:700'>{escape(holding['name'])} "
                        f"<span style='color:#8a857b;font-weight:400'>{'보유' if holding['held'] else '관심'}"
                        f" · {escape(holding['market_label'])}</span></p>"
                        "<ul style='margin:0;padding-left:18px'>" + "".join(link(m) for m in holding["matches"]) + "</ul>")
        if not (personal["alerts"] or personal["holdings"]):
            part.append("<p style='margin:0;color:#4c5a6b'>새로 넘은 점검 기준도, 내 종목이 나온 기사도 없습니다.</p>")
        body.append("".join(part))
    briefing = digest.get("briefing")
    if briefing:
        # 메일 프로그램이 <style>을 버리므로 위 섹션들과 같은 인라인 스타일을 쓴다.
        body.append(f"<p style='margin:14px 0 0;font-size:16px'>{escape(briefing['lead'])}</p>")
        for item in briefing["items"]:
            body.append(f"<h2 style='font-size:17px;margin:24px 0 6px'>{escape(item['title'])}</h2>"
                        f"<p style='margin:0 0 6px'>{escape(item['body'])}</p>"
                        "<ul style='margin:0;padding-left:18px;font-size:14px'>"
                        + "".join(link(n) for n in _sources(item["sources"])) + "</ul>")
        body.append("<h2 style='font-size:17px;margin:24px 0 6px'>오늘 지켜볼 것</h2>"
                    "<ul style='margin:0;padding-left:18px'>"
                    + "".join(f"<li style='margin:0 0 4px'>{escape(p['text'])}</li>" for p in briefing["watch"])
                    + "</ul>")
    for s in ([] if briefing else digest["sections"]):
        part = [f"<h2 style='font-size:17px;margin:24px 0 6px'>{escape(s['name'])}{tone(s['tone'])}</h2>"]
        if s["summary"]:
            part.append(f"<p style='margin:0 0 8px;color:#4c5a6b'>{escape(s['summary'])}</p>")
        if s["news"]:
            part.append("<ul style='margin:6px 0 0;padding-left:18px'>" + "".join(link(i) for i in s["news"]) + "</ul>")
        body.append("".join(part))
    body.append("<p style='margin:24px 0 0;font-size:14px;color:#4c5a6b'>시장 논조: "
                + " · ".join(escape(t["name"]) + tone(t["tone"]) for t in digest["tones"]) + "</p>")
    if digest.get("video"):
        video = digest["video"]
        body.append(f"<p style='margin:6px 0 0;font-size:14px;color:#4c5a6b'>오늘의 영상: "
                    f"<a href='{escape(video['url'], quote=True)}' style='color:#16324f'>"
                    f"{escape(video['title'])}</a></p>")
    title = digest["title"]
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
