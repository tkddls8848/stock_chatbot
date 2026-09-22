from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime
import json
import logging
from pathlib import Path
import tempfile
from typing import Any

from .client import PolymarketWebClient, SourceError
from .config import Settings
from .highlights import select_issues, write_issues
from .markets import shortlist, prepare_issue
from .media import background_for
from .render import find_font, probe_duration, render_video
from .review import write_json, write_review
from .scenario import Scenario, Scene, build_scenario
from .tts import synthesize


logger = logging.getLogger(__name__)


def produce_editorial(plan_path: Path, settings: Settings) -> ProductionResult:
    """확정된 제작 원고만 렌더한다. 원자료 재조회·재요약을 하지 않는다."""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rows = plan.get("scenes", [])
    sectors = {row.get("sector_key") for row in rows if row.get("kind") == "consensus"}
    if (plan.get("schema_version") != 1 or len(rows) != 7
            or sectors != {"macro", "geopolitics", "general", "composite", "equities"}
            or rows[0].get("kind") != "intro" or rows[-1].get("kind") != "outro"):
        raise ValueError("제작 원고는 도입·5개 분야·마무리를 포함해야 합니다")
    for row in rows:
        if not str(row.get("narration", "")).strip() or not row.get("evidence"):
            raise ValueError("모든 장면에 내레이션과 입력 근거가 필요합니다")
    if not (str(plan.get("title", "")).strip()
            and str(plan.get("description", "")).strip() and plan.get("tags")):
        raise ValueError("제작 원고에 게시 제목·설명·태그가 필요합니다")
    names = {field.name for field in fields(Scene)}
    scenario = Scenario(
        date=plan["date"], generation_id=plan["generation_id"],
        source_written_at=plan["source_written_at"],
        scenes=tuple(Scene(**{k: v for k, v in row.items() if k in names}) for row in rows),
    )
    result = produce_revision(scenario, editorial_metadata(plan, scenario), plan_path.parent, settings)
    production = _read_json(plan_path.parent / "production.json")
    production["source_plan"] = str(plan_path)
    write_json(plan_path.parent / "production.json", production)
    return result


def prepare_daily(settings: Settings, today: date, day_dir: Path) -> Scenario | None:
    """최대 두 번의 모델 호출만 쓰고, 제작 판단의 원자료를 렌더 전에 보존한다."""
    client = PolymarketWebClient(settings.web_url)
    snapshot = client.snapshot()
    candidates, audit = shortlist(snapshot)
    audit.update({"generation_id": snapshot.generation_id, "generated_at": snapshot.summary["generated_at"],
                  "candidates": candidates, "selected": [], "rejected": [], "llm_calls": 0})
    day_dir.mkdir(parents=True, exist_ok=True)
    # 실패한 단계와 이미 지출한 조회도 다시 확인할 수 있도록 단계마다 원자적으로 저장한다.
    def save():
        audit["requests"] = dict(client.requests)
        write_json(day_dir / "selection.json", audit)
    save()
    try:
        if candidates:
            audit["llm_calls"] += 1
            save()
        selected = select_issues(candidates, settings, rejected=audit["rejected"])
        audit["selected"] = selected
        save()
        issues = []
        for candidate in selected:
            detail = client.detail(candidate["id"], snapshot.generation_id)
            # 잘못된 개별 가격은 해당 이슈만 제외한다. 세대 불일치는 위 detail에서 중단한다.
            try:
                issue = prepare_issue(candidate, detail, [])
            except SourceError as exc:
                audit["rejected"].append({"id": candidate["id"], "reason": str(exc)})
                continue
            issue["news"] = client.news(candidate["title"], reference=snapshot.summary["generated_at"])
            issues.append(issue)
        client.confirm(snapshot.generation_id)
        write_json(day_dir / "source.json", {"summary": snapshot.summary, "issues": issues})
        if not issues:
            audit["status"] = "no_suitable_issues"
            save()
            return None
        audit["llm_calls"] += 1
        save()
        scripts = write_issues(issues, settings)
        scenario = build_scenario(snapshot, issues, scripts, production_date=today)
        audit.update({"status": "script_ready", "scripts": scripts, "produced_issues": len(issues)})
        save()
        write_json(day_dir / "scenario.json", scenario.to_dict())
        return scenario
    except Exception as exc:
        audit["status"] = "failed"
        audit["error"] = str(exc)
        raise
    finally:
        save()



def produce_revision(
    scenario: Scenario, metadata: dict[str, Any], target: Path, settings: Settings,
) -> ProductionResult:
    """고정된 시나리오를 하나의 연속 음성으로 렌더하고 새 검수를 요구한다."""
    work = target / "media"
    work.mkdir(parents=True, exist_ok=True)
    # 편집 단위는 장면으로 유지하지만 최종 음성은 전체 원고를 한 번에 합성한다.
    # 장면별 TTS를 잘라 이어 붙이면 경계마다 음색과 호흡이 다시 시작된다.
    audio, spoken = work / "narration.mp3", work / "narration.words.jsonl"
    scene_words = synthesize(
        [scene.narration for scene in scenario.scenes], audio_path=audio, words_path=spoken,
        voice=settings.tts_voice, rate=settings.tts_rate,
    )
    backgrounds = tuple(
        background_for(scene.kind, scene.visual_query) if settings.visuals_enabled else None
        for scene in scenario.scenes
    )
    video = target / f"nunchi-editorial-{scenario.date}.mp4"
    duration = render_video(
        scenario, audio_path=audio, scene_words=scene_words, output_path=video,
        work_dir=work, font_path=find_font(settings.font_file),
        ffmpeg_bin=settings.ffmpeg_bin, ffprobe_bin=settings.ffprobe_bin,
        max_duration=settings.max_duration_seconds, background_paths=backgrounds,
    )
    script = write_review(
        target, scenario=scenario, metadata=metadata, video=video,
        duration=duration, timezone=settings.timezone,
    )
    write_json(target / "scenario.json", scenario.to_dict())
    write_json(target / "production.json", {
        "title": metadata["title"], "generation_id": scenario.generation_id,
        "duration_seconds": duration, "voice": settings.tts_voice, "rate": settings.tts_rate,
        "synthesis": "continuous",
        "audio": str(audio), "words": str(spoken), "video": str(video),
    })
    return ProductionResult(
        status="pending_review", date=scenario.date,
        video_path=str(video), review_path=str(script),
    )


@dataclass(frozen=True)
class ProductionResult:
    status: str
    date: str
    video_path: str | None = None
    review_path: str | None = None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def editorial_metadata(plan: dict[str, Any], scenario: Scenario) -> dict[str, Any]:
    """게시 문구의 원본은 제작 원고다. 검수 피드백은 원고를 고쳐 반영한다."""
    return {
        "title": f"{plan['title']} | {scenario.date.replace('-', '.')} #Shorts",
        "description": plan["description"],
        "tags": list(plan["tags"]),
    }


def metadata_for(scenario: Scenario) -> dict[str, Any]:
    stamp = scenario.date.replace("-", ".")
    labels = [scene.title for scene in scenario.scenes if scene.kind == "consensus"]
    # A concrete question promises an explanation without mistaking turnover for inflows.
    headline = (
        " ".join(scenario.scenes[0].title.split())
        if scenario.scenes and scenario.scenes[0].evidence
        else f"{scenario.lead_label} {scenario.lead_volume} 거래, 전망도 확실할까?"
        if scenario.lead_label and scenario.lead_volume
        else "지난 24시간 예측시장에서 돈이 몰린 곳"
    )
    return {
        "title": f"{headline} | {stamp} #Shorts",
        "description": (
            "거래가 활발하고 시장 관련성이 높은 개별 예측시장 이슈를 골랐습니다.\n\n"
            f"오늘 다룬 이슈: {', '.join(labels)}\n"
            f"원자료 기준 시각: {scenario.source_written_at}\n"
            + "\n".join(scene.source_url for scene in scenario.scenes if scene.source_url) + "\n"
            +
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
            review_path=previous.get("review_path"),
        )

    day_dir = settings.output_dir / day
    scenario = prepare_daily(settings, today, day_dir)
    if scenario is None:
        return ProductionResult(status="no_suitable_issues", date=day)
    video_path = day_dir / f"polymarket-{day}.mp4"
    scenario_path = day_dir / "scenario.json"
    metadata = metadata_for(scenario)
    backgrounds = tuple(
        background_for(scene.kind, scene.visual_query) if settings.visuals_enabled else None
        for scene in scenario.scenes
    )

    with tempfile.TemporaryDirectory(prefix=f".{day}-", dir=settings.output_dir) as raw_work:
        work = Path(raw_work)
        audio = work / "narration.mp3"
        spoken = work / "narration.words.jsonl"
        scene_words = synthesize(
            [scene.narration for scene in scenario.scenes],
            audio_path=audio,
            words_path=spoken,
            voice=settings.tts_voice,
            rate=settings.tts_rate,
        )
        measured = probe_duration(audio, ffprobe_bin=settings.ffprobe_bin)
        if measured > settings.max_duration_seconds:
            logger.info(
                "내레이션 %.1f초가 목표 길이를 넘지만 원래 속도와 전체 음성을 유지합니다",
                measured,
            )
        duration = render_video(
            scenario,
            audio_path=audio,
            scene_words=scene_words,
            output_path=video_path,
            work_dir=work,
            font_path=find_font(settings.font_file),
            ffmpeg_bin=settings.ffmpeg_bin,
            ffprobe_bin=settings.ffprobe_bin,
            max_duration=settings.max_duration_seconds,
            background_paths=backgrounds,
        )

    scenario_payload = {**scenario.to_dict(), "duration_seconds": round(duration, 3)}
    scenario_payload["visuals"] = [
        {"asset": path.name, "source": "GPT Image / built-in", "generated": True}
        if path else None for path in backgrounds
    ]
    write_json(scenario_path, scenario_payload)
    # 게시 문구와 멘트는 review.md 한 장에서 검수한다.
    script = write_review(
        day_dir, scenario=scenario, metadata=metadata, video=video_path,
        duration=duration, timezone=settings.timezone,
    )

    days = state.setdefault("days", {})
    days[day] = {
        "generation_id": scenario.generation_id,
        "produced_at": datetime.now(settings.timezone).isoformat(),
        "video_path": str(video_path),
        "review_path": str(script),
        "duration_seconds": round(duration, 3),
    }
    # 상태 파일이 끝없이 커지지 않도록 최근 90일만 보존한다.
    state["days"] = dict(sorted(days.items())[-90:])
    write_json(settings.state_file, state)
    # 생성 직후에는 review.md로 자연어 검수를 이어간다.
    return ProductionResult(
        status="pending_review",
        date=day,
        video_path=str(video_path),
        review_path=str(script),
    )
