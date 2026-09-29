from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
import json
import logging
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

from .client import PolymarketWebClient, SourceError
from .clips import clips_for, review_details, visual_payload
from .config import Settings
from .highlights import select_issues, write_issues
from .markets import _topic, shortlist, prepare_issue
from .media import backgrounds_for
from .render import find_font, probe_duration, render_video
from .review import operation_lock, write_json, write_review
from .scenario import Scenario, Scene, build_scenario
from .tts import synthesize


logger = logging.getLogger(__name__)


def produce_editorial(plan_path: Path, settings: Settings) -> ProductionResult:
    """확정된 제작 원고만 렌더한다. 원자료 재조회·재요약을 하지 않는다."""
    with operation_lock(plan_path.parent, ".workflow.lock"):
        return _produce_editorial(plan_path, settings)


def _produce_editorial(plan_path: Path, settings: Settings) -> ProductionResult:
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


def recently_featured(settings: Settings, today: date) -> tuple[set[str], set[str]]:
    """최근 `repeat_days`일(오늘 제외)에 영상으로 다룬 이벤트 ID와 주제.

    2026-09-23~27 영상 네 편이 모두 같은 연준·호르무즈 질문이었다 — 참여가 가장 몰린
    이슈를 매일 다시 골랐기 때문이다. 날짜별 폴더의 원자료(`source.json`, 없으면
    `scenario.json`)를 읽으므로 이 규칙 이전에 만든 영상도 센다. 주제는 날짜·숫자를
    뺀 제목 낱말(`_topic`)이라 "9월 30일까지"와 "12월 31일까지" 변형도 같은 주제다.
    """
    ids: set[str] = set()
    topics: set[str] = set()
    for back in range(1, settings.repeat_days + 1):
        day_dir = settings.output_dir / (today - timedelta(days=back)).isoformat()
        source = _read_json(day_dir / "source.json")
        for issue in source.get("issues") or []:
            if isinstance(issue, dict) and issue.get("id"):
                ids.add(str(issue["id"]))
                topics.add(_topic(str(issue.get("title") or "")))
        for scene in _read_json(day_dir / "scenario.json").get("scenes") or []:
            if isinstance(scene, dict) and scene.get("event_id"):
                ids.add(str(scene["event_id"]))
    topics.discard("")
    return ids, topics


def prepare_daily(settings: Settings, today: date, day_dir: Path) -> Scenario | None:
    """최대 두 번의 모델 호출만 쓰고, 제작 판단의 원자료를 렌더 전에 보존한다."""
    client = PolymarketWebClient(settings.web_url)
    snapshot = client.snapshot()
    candidates, audit = shortlist(snapshot)
    used_ids, used_topics = recently_featured(settings, today)
    fresh = [row for row in candidates
             if str(row["id"]) not in used_ids and row.get("topic_key") not in used_topics]
    audit["recently_featured"] = [
        {"id": row["id"], "title": row.get("title"), "reason": "최근 영상에서 다룬 이슈"}
        for row in candidates if row not in fresh
    ]
    candidates = fresh
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
        written = {script["id"] for script in scripts}
        for issue in issues:
            if issue["id"] not in written:
                audit["rejected"].append({"id": issue["id"], "reason": "원고 검증 실패(교정 후)"})
        issues = [issue for issue in issues if issue["id"] in written]
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
        voice=settings.tts_voice, rate=settings.tts_rate, ffmpeg_bin=settings.ffmpeg_bin,
    )
    backgrounds = (
        backgrounds_for(scenario.scenes, (
            target.parent.parent if settings.generated_clips and settings.video_api_key
            and target.parent.name == "revisions" else target
        ) / "backgrounds", settings) if settings.visuals_enabled
        else tuple(None for _ in scenario.scenes)
    )
    backgrounds = clips_for(scenario.scenes, backgrounds, settings)
    video = target / f"nunchi-editorial-{scenario.date}.mp4"
    duration = render_video(
        scenario, audio_path=audio, scene_words=scene_words, output_path=video,
        work_dir=work, font_path=find_font(settings.font_file),
        blender_bin=settings.blender_bin, ffprobe_bin=settings.ffprobe_bin,
        max_duration=settings.max_duration_seconds, background_paths=backgrounds,
    )
    script = write_review(
        target, scenario=scenario, metadata=metadata, video=video,
        duration=duration, timezone=settings.timezone,
        **review_details(backgrounds),
    )
    payload = scenario.to_dict()
    if any(path and path.suffix == ".mp4" for path in backgrounds):
        payload["visuals"] = visual_payload(backgrounds)
    write_json(target / "scenario.json", payload)
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
        "title": video_title(scenario.date),
        "description": plan["description"],
        "tags": list(plan["tags"]),
    }


def video_title(date_text: str) -> str:
    """게시 제목은 "yyyy-mm-dd 시장 컨센서스"로 통일한다(운영자 결정 2026-09-27).

    그날 다룬 이슈는 설명에 적는다. 자연어 편집으로도 바꾸지 않는다.
    """
    return f"{date_text} 시장 컨센서스"


def info_time(stamp: str) -> str:
    """설명란의 기준 시각. ISO 원문(마이크로초·오프셋) 대신 한국 시각을 분까지만 적는다
    (운영자 결정 2026-09-28: "정보 기준 시각: 2026-09-28 18:00")."""
    return datetime.fromisoformat(stamp).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")


# 설명란 맨 위에 두는 사이트 주소. 더보기를 펼치지 않아도 보이는 자리다(운영자 결정 2026-09-29).
SITE_URL = "https://nunchi.live"
# 사업 문의 줄. 한국어판·영어판 모두 영문 한 줄로 단다(운영자 결정 2026-09-29).
CONTACT_LINE = "Business inquiries: tkddls8848@gmail.com"


def metadata_for(scenario: Scenario) -> dict[str, Any]:
    labels = [scene.title for scene in scenario.scenes if scene.kind == "consensus"]
    return {
        "title": video_title(scenario.date),
        "description": (
            f"질문별 조건과 전체 컨센서스는 여기서 확인하세요 👉 {SITE_URL}\n{CONTACT_LINE}\n\n"
            "참여가 활발하고 금융시장과 관련이 깊은 집단 예측 컨센서스 이슈를 골랐습니다.\n\n"
            f"오늘 다룬 이슈: {', '.join(labels)}\n"
            f"정보 기준 시각: {info_time(scenario.source_written_at)}\n"
            "확률은 해외 집단 예측 참여자들의 전망을 모은 값이며, 사실 확정이나 "
            "투자 조언이 아닙니다.\n\n#집단예측 #컨센서스 #경제전망 #Shorts"
        ),
        "tags": ["집단 예측", "컨센서스", "경제 전망", "경제", "지정학", "Shorts"],
    }


def produce_daily(
    settings: Settings,
    *,
    production_date: date | None = None,
    force: bool = False,
) -> ProductionResult:
    today = production_date or datetime.now(settings.timezone).date()
    # 예약 제작·강제 재제작이 업로드 중인 원본을 바꾸지 못하게 한다.
    with operation_lock(settings.output_dir / today.isoformat(), ".workflow.lock"):
        return _produce_daily(settings, today=today, force=force)


def _produce_daily(settings: Settings, *, today: date, force: bool) -> ProductionResult:
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
    backgrounds = (
        backgrounds_for(scenario.scenes, day_dir / "backgrounds", settings) if settings.visuals_enabled
        else tuple(None for _ in scenario.scenes)
    )
    backgrounds = clips_for(scenario.scenes, backgrounds, settings)

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
            ffmpeg_bin=settings.ffmpeg_bin,
        )
        measured = probe_duration(audio, ffprobe_bin=settings.ffprobe_bin)
        if measured > settings.max_duration_seconds:
            logger.warning(
                "내레이션 %.1f초가 허용 길이 %.0f초를 넘지만 원래 속도와 전체 음성을 유지합니다",
                measured, settings.max_duration_seconds,
            )
        duration = render_video(
            scenario,
            audio_path=audio,
            scene_words=scene_words,
            output_path=video_path,
            work_dir=work,
            font_path=find_font(settings.font_file),
            blender_bin=settings.blender_bin,
            ffprobe_bin=settings.ffprobe_bin,
            max_duration=settings.max_duration_seconds,
            background_paths=backgrounds,
        )

    scenario_payload = {**scenario.to_dict(), "duration_seconds": round(duration, 3)}
    scenario_payload["visuals"] = visual_payload(backgrounds)
    write_json(scenario_path, scenario_payload)
    # 게시 문구와 멘트는 review.md 한 장에서 검수한다.
    script = write_review(
        day_dir, scenario=scenario, metadata=metadata, video=video_path,
        duration=duration, timezone=settings.timezone,
        **review_details(backgrounds),
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
