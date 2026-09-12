from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import logging
from pathlib import Path
import tempfile
from typing import Any

from .client import PolymarketWebClient
from .config import Settings
from .render import find_font, probe_duration, render_video
from .scenario import Scenario, build_scenario
from .tts import synthesize
from .youtube import upload_video


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductionResult:
    status: str
    date: str
    video_path: str | None = None
    youtube_id: str | None = None


def _faster_rate(rate: str, actual: float, target: float) -> str:
    """실측 길이가 상한을 넘을 때 필요한 만큼만 TTS 속도를 높인다."""
    try:
        base = int(rate.rstrip("%"))
    except ValueError:
        base = 0
    required = round(((1 + base / 100) * actual / target - 1) * 100) + 2
    return f"{max(-50, min(35, required)):+d}%"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def metadata_for(scenario: Scenario) -> dict[str, Any]:
    stamp = scenario.date.replace("-", ".")
    labels = [scene.title for scene in scenario.scenes if scene.kind == "consensus"]
    return {
        "title": f"오늘의 폴리마켓 컨센서스 | {stamp} #Shorts",
        "description": (
            "경제·금융·지정학 예측시장의 현재 컨센서스를 요약했습니다.\n\n"
            f"오늘 다룬 분야: {', '.join(labels)}\n"
            "확률은 Polymarket 참여자의 베팅 가격이 암시하는 값이며, 사실 확정이나 "
            "투자 조언이 아닙니다.\n\n#폴리마켓 #예측시장 #시장컨센서스 #Shorts"
        ),
        "tags": ["폴리마켓", "예측시장", "시장 컨센서스", "경제", "지정학", "Shorts"],
    }


def produce_daily(
    settings: Settings,
    *,
    production_date: date | None = None,
    force: bool = False,
    upload: bool | None = None,
) -> ProductionResult:
    today = production_date or datetime.now(settings.timezone).date()
    day = today.isoformat()
    state = _read_json(settings.state_file)
    previous = (state.get("days") or {}).get(day) if isinstance(state.get("days"), dict) else None
    if previous and not force:
        return ProductionResult(
            status="already_produced",
            date=day,
            video_path=previous.get("video_path"),
            youtube_id=previous.get("youtube_id"),
        )

    snapshot = PolymarketWebClient(settings.web_url).snapshot()
    scenario = build_scenario(
        snapshot,
        production_date=today,
        target_chars=settings.target_script_chars,
        max_groups=settings.max_groups,
    )
    day_dir = settings.output_dir / day
    day_dir.mkdir(parents=True, exist_ok=True)
    video_path = day_dir / f"polymarket-{day}.mp4"
    scenario_path = day_dir / "scenario.json"
    metadata_path = day_dir / "youtube.json"
    metadata = metadata_for(scenario)

    with tempfile.TemporaryDirectory(prefix=f".{day}-", dir=settings.output_dir) as raw_work:
        work = Path(raw_work)
        audio = work / "narration.mp3"
        subtitles = work / "captions.vtt"
        synthesize(
            scenario.narration,
            audio_path=audio,
            subtitle_path=subtitles,
            voice=settings.tts_voice,
            rate=settings.tts_rate,
        )
        measured = probe_duration(audio, ffprobe_bin=settings.ffprobe_bin)
        if measured > settings.max_duration_seconds:
            adjusted_rate = _faster_rate(
                settings.tts_rate,
                measured,
                settings.max_duration_seconds - 2,
            )
            logger.info(
                "내레이션 %.1f초가 상한을 넘어 TTS 속도를 %s로 한 번 조정합니다",
                measured,
                adjusted_rate,
            )
            synthesize(
                scenario.narration,
                audio_path=audio,
                subtitle_path=subtitles,
                voice=settings.tts_voice,
                rate=adjusted_rate,
            )
        duration = render_video(
            scenario,
            audio_path=audio,
            subtitle_path=subtitles,
            output_path=video_path,
            work_dir=work,
            font_path=find_font(settings.font_file),
            ffmpeg_bin=settings.ffmpeg_bin,
            ffprobe_bin=settings.ffprobe_bin,
            max_duration=settings.max_duration_seconds,
        )

    scenario_payload = {**scenario.to_dict(), "duration_seconds": round(duration, 3)}
    _write_json(scenario_path, scenario_payload)
    _write_json(metadata_path, metadata)

    should_upload = settings.upload_enabled if upload is None else upload
    youtube_id = None
    if should_upload:
        youtube_id = upload_video(
            video_path,
            metadata,
            token_file=settings.youtube_token_file,
            client_secret_file=settings.youtube_client_secret_file,
            privacy=settings.youtube_privacy,
        )

    days = state.setdefault("days", {})
    days[day] = {
        "generation_id": scenario.generation_id,
        "produced_at": datetime.now(settings.timezone).isoformat(),
        "video_path": str(video_path),
        "youtube_id": youtube_id,
        "duration_seconds": round(duration, 3),
    }
    # 상태 파일이 끝없이 커지지 않도록 최근 90일만 보존한다.
    state["days"] = dict(sorted(days.items())[-90:])
    _write_json(settings.state_file, state)
    return ProductionResult(
        status="uploaded" if youtube_id else "produced",
        date=day,
        video_path=str(video_path),
        youtube_id=youtube_id,
    )
