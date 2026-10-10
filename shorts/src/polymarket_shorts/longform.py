"""시장상황 보고서 한 편을 4~5분짜리 가로 롱폼 영상으로 만든다(운영자 결정 2026-10-10 — 영어판 쇼츠를 대신한다).

원재료는 공개 웹에 실린 것뿐이다. 봇이 발행한 보고서 본문(`/api/search`의 `kind: report`), 같은 시장의 그 구간
기사(보고서가 근거로 고른 기사), 일일 뉴스 감성(`/api/market`). 모델을 부르지 않는다 — 보고서 본문이 이미
모델이 쓰고 서버가 다듬은 글이라, 원고는 그 글을 합쇼체로 옮겨 읽고 앞뒤에 자료에 있는 숫자만 덧붙인다.
새 사실을 만들어 넣을 자리가 없다.

순서: 도입(고정 화면·자막 없음) → 최근 2주 뉴스 감성 → 보고서 문단들 → 그 구간 주요 기사 → 마무리(고정 화면·자막 없음).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import logging
from pathlib import Path
import re
import shutil
from typing import Any

from .client import PolymarketWebClient
from .config import Settings
from .core.storage import write_json
from .speech import end_sentence, to_polite_text


logger = logging.getLogger(__name__)

MARKETS = {"CN": "중국", "HK": "홍콩", "US": "미국", "KR": "한국", "JP": "일본", "EU": "유럽"}
SENTIMENT_DAYS = 14
MAX_HEADLINES = 7
_ORDINALS = ("첫째", "둘째", "셋째", "넷째", "다섯째", "여섯째", "일곱째")
_WINDOW = re.compile(r"(?:(\d{2})-(\d{2}) )?(\d{2}):(\d{2})~(?:(\d{2})-(\d{2}) )?(\d{2}):(\d{2})")
CLOSING_LINE = ("이 영상은 눈치가 모은 뉴스를 바탕으로 한 시장 요약이며, 투자 조언이 아닙니다. "
                "자세한 내용은 눈치 닷 라이브에서 확인하세요.")


class LongformError(RuntimeError):
    pass


@dataclass(frozen=True)
class Segment:
    """롱폼 한 장면. `sentences`는 화면에 한 줄씩 짚어 가는 단위이고 이어 붙인 것이 그 장면의 내레이션이다."""

    kind: str                       # intro · sentiment · report · headlines · outro
    chapter: str                    # 오른쪽 목차에 보이는 이름
    sentences: tuple[str, ...]
    # 장면이 그리는 자료. sentiment: [{date, value, count}], headlines: [{title, source, time}]
    rows: tuple[dict[str, Any], ...] = ()
    # 장면 안에서 처음 몇 문장은 화면 줄과 짝이 없다(headlines의 여는 말). 그다음 문장부터 rows와 한 줄씩 짝이다.
    lead: int = 0

    @property
    def narration(self) -> str:
        return " ".join(self.sentences)


@dataclass(frozen=True)
class Longform:
    market: str
    report_id: str
    published_at: str
    window: str
    segments: tuple[Segment, ...]
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return MARKETS.get(self.market, self.market)

    @property
    def narration(self) -> str:
        return "\n".join(segment.narration for segment in self.segments)

    def to_dict(self) -> dict[str, Any]:
        return {"market": self.market, "report_id": self.report_id, "published_at": self.published_at,
                "window": self.window, "segments": [asdict(segment) for segment in self.segments],
                "narration": self.narration, "characters": len(self.narration.replace("\n", " ")),
                "evidence": self.evidence}


def sentences(text: str) -> tuple[str, ...]:
    """마침표·물음표 뒤 공백에서 나눈다. "5.3%"처럼 숫자 사이 마침표는 공백이 없어 나뉘지 않는다."""
    return tuple(part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip())


def report_window(report: dict[str, Any]) -> tuple[datetime, datetime]:
    """보고서 제목의 구간("20:01~08:00 UTC +9", 24시간이 넘으면 "10-08 15:00~10-10 08:00 UTC +9")을 시각으로 푼다.

    끝은 발행 시각이다. 날짜가 없는 시작이 끝보다 늦으면 전날이다.
    """
    end = datetime.fromisoformat(str(report["published_at"]))
    match = _WINDOW.search(str(report.get("title") or ""))
    if not match:
        raise LongformError("보고서 제목에서 구간을 읽지 못했습니다: " + str(report.get("title")))
    month, day, hour, minute = match.group(1, 2, 3, 4)
    start = end.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if month:
        start = start.replace(month=int(month), day=int(day))
        if start > end:
            start = start.replace(year=start.year - 1)
    elif start > end:
        start -= timedelta(days=1)
    return start, end


def _signed(value: float) -> str:
    """음성으로 읽을 감성 점수. TTS는 "-0.11"의 빼기표를 읽지 않거나 "하이픈"으로 읽는다."""
    return f"마이너스 {abs(value):.2f}" if value < 0 else f"{value:.2f}"


def _clock(moment: datetime) -> str:
    """음성으로 읽을 시각. "20:01"은 "오후 8시 1분"이다."""
    hour = moment.hour % 12 or 12
    return f"{'오전' if moment.hour < 12 else '오후'} {hour}시" + (f" {moment.minute}분" if moment.minute else "")


def _day(stamp: str) -> str:
    moment = datetime.fromisoformat(stamp) if "T" in stamp else datetime.strptime(stamp, "%Y-%m-%d")
    return f"{moment.month}월 {moment.day}일"


def _sentiment_segment(label: str, daily: list[dict[str, Any]]) -> Segment | None:
    recent = daily[-SENTIMENT_DAYS:]
    if len(recent) < 3:
        return None
    values = [float(row["avg_sentiment"]) for row in recent]
    high = max(recent, key=lambda row: row["avg_sentiment"])
    low = min(recent, key=lambda row: row["avg_sentiment"])
    latest = recent[-1]
    lines = [
        f"먼저 최근 {len(recent)}일 동안 {label} 뉴스 감성 흐름입니다.",
        "감성 점수는 마이너스 1부터 1 사이이고, 0보다 크면 그날 기사의 논조가 긍정 쪽으로 기울었다는 뜻입니다.",
        # 숫자 뒤에 조사를 붙이지 않는다 — "0.06였고"처럼 읽는 소리와 받침이 어긋난다. "…입니다"는 어디에나 붙는다.
        f"이 기간 평균은 {_signed(sum(values) / len(values))}입니다.",
        f"가장 높았던 날은 {_day(high['date'])}, {_signed(high['avg_sentiment'])}이고, "
        f"가장 낮았던 날은 {_day(low['date'])}, {_signed(low['avg_sentiment'])}입니다.",
    ]
    summary = str(latest.get("summary") or "").strip()
    if summary:
        lines.append(f"가장 최근 집계인 {_day(latest['date'])} 요약은 이렇습니다.")
        lines.extend(sentences(end_sentence(to_polite_text(summary))))
    rows = tuple({"date": row["date"], "value": float(row["avg_sentiment"]), "count": int(row.get("count") or 0)}
                 for row in recent)
    return Segment("sentiment", "뉴스 감성 흐름", tuple(lines), rows=rows)


def _headline_segment(news: list[dict[str, Any]]) -> Segment | None:
    picked = news[:MAX_HEADLINES]
    if not picked:
        return None
    lines = [f"이번 구간에 나온 주요 기사 {len(picked)}건의 제목도 짚어 보겠습니다."]
    rows = []
    for ordinal, row in zip(_ORDINALS, picked):
        title = re.sub(r"\s+", " ", str(row["title"])).strip()
        lines.append(end_sentence(f"{ordinal}, {title}"))
        moment = datetime.fromisoformat(row["published_at"])
        rows.append({"title": title, "source": str(row.get("source") or ""), "time": f"{moment:%m/%d %H:%M}",
                     "url": str(row.get("url") or "")})
    return Segment("headlines", "주요 기사", tuple(lines), rows=tuple(rows), lead=1)


def build_longform(report: dict[str, Any], news: list[dict[str, Any]], daily: list[dict[str, Any]]) -> Longform:
    market = str(report["market"])
    label = MARKETS.get(market, market)
    published = datetime.fromisoformat(str(report["published_at"]))
    match = _WINDOW.search(str(report.get("title") or ""))
    window = match.group(0) if match else ""
    paragraphs = [paragraph for paragraph in re.split(r"\n\s*\n", str(report["text"]).strip()) if paragraph.strip()]
    if not paragraphs:
        raise LongformError("보고서 본문이 비었습니다")
    start, _ = report_window(report)
    # 장 시점 이름(한국장 개장 전 등)은 말하지 않는다. 봇의 시점 이름은 한국 시각 기준이라 미국 보고서에서는
    # 본문의 말("미국장 개장 전")과 어긋났다. 실제 구간만 말한다.
    intro = (
        f"{published.month}월 {published.day}일 {_clock(published)}에 나온 {label} 시장상황 보고서입니다.",
        f"{_day(start.isoformat())} {_clock(start)}부터 {_day(published.isoformat())} {_clock(published)}까지 "
        "모인 뉴스를 바탕으로 썼습니다.",
        "최근 뉴스 감성 흐름을 먼저 보고, 보고서 본문과 이 구간의 주요 기사를 차례로 짚어 보겠습니다.",
    )
    segments = [Segment("intro", "시작", intro)]
    sentiment = _sentiment_segment(label, daily)
    if sentiment:
        segments.append(sentiment)
    for number, paragraph in enumerate(paragraphs, start=1):
        body = sentences(to_polite_text(re.sub(r"\s+", " ", paragraph)))
        if number == 1:
            body = ("이제 보고서 본문입니다.", *body)
        segments.append(Segment("report", f"보고서 {number}", tuple(end_sentence(line) for line in body),
                                lead=1 if number == 1 else 0))
    headlines = _headline_segment(news)
    if headlines:
        segments.append(headlines)
    segments.append(Segment("outro", "마무리", tuple(sentences(CLOSING_LINE))))
    evidence = {
        "report": {key: report.get(key) for key in ("id", "title", "published_at", "source", "text")},
        "headlines": [{key: row.get(key) for key in ("id", "title", "text", "source", "published_at", "url")}
                      for row in news[:MAX_HEADLINES]],
        "sentiment": [{key: row.get(key) for key in ("date", "avg_sentiment", "count", "summary")}
                      for row in daily[-SENTIMENT_DAYS:]],
    }
    return Longform(market, str(report["id"]), published.isoformat(), window, tuple(segments), evidence)


@dataclass(frozen=True)
class LongformResult:
    status: str
    market: str
    report_id: str
    video_path: str | None = None
    duration_seconds: float | None = None
    characters: int | None = None


def longform_root(settings: Settings) -> Path:
    return settings.output_dir / "longform"


def _target(settings: Settings, longform: Longform) -> Path:
    published = datetime.fromisoformat(longform.published_at)
    return longform_root(settings) / f"{published:%Y-%m-%d}" / f"{longform.market}-{published:%H%M}"


def produce_longform(settings: Settings, market: str, *, report_id: str = "", force: bool = False,
                     client: PolymarketWebClient | None = None) -> LongformResult:
    """보고서 한 편을 골라 원고·음성·영상을 만든다. 같은 보고서의 영상이 있으면 `force` 없이는 다시 만들지 않는다."""
    from .render import find_font
    from .longform_render import render_longform
    from .tts import synthesize

    market = market.upper()
    if market not in MARKETS:
        raise LongformError(f"알 수 없는 시장입니다: {market}")
    client = client or PolymarketWebClient(settings.web_url)
    report = client.market_report(market, report_id)
    start, end = report_window(report)
    longform = build_longform(report, client.report_news(market, start, end), client.market_sentiment(market))
    target = _target(settings, longform)
    video = target / f"report-{market.lower()}-{datetime.fromisoformat(longform.published_at):%Y%m%d-%H%M}.mp4"
    if video.is_file() and not force:
        return LongformResult("already_produced", market, longform.report_id, str(video))
    work = target / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    write_json(target / "source.json", longform.evidence)
    write_json(target / "scenario.json", longform.to_dict())
    audio = target / "narration.mp3"
    words = synthesize([segment.narration for segment in longform.segments], audio_path=audio,
                       words_path=target / "words.jsonl", voice=settings.tts_voice, rate=settings.tts_rate,
                       ffmpeg_bin=settings.ffmpeg_bin)
    duration = render_longform(longform, audio_path=audio, segment_words=words, output_path=video, work_dir=work,
                               font_path=find_font(settings.font_file), blender_bin=settings.blender_bin,
                               ffprobe_bin=settings.ffprobe_bin)
    characters = longform.to_dict()["characters"]
    write_json(target / "result.json", {"video": video.name, "duration_seconds": round(duration, 2),
                                               "characters": characters, "report_id": longform.report_id})
    shutil.rmtree(work)
    logger.info("롱폼 완료 %s %.1f초 %d자", video, duration, characters)
    return LongformResult("produced", market, longform.report_id, str(video), round(duration, 2), characters)
