from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta
import html
import json
from pathlib import Path
import re
import shutil
from typing import Any

from .config import PROJECT_DIR, Settings
from .render import probe_duration
from .tts import synthesize


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TimedScene:
    start: float
    duration: float
    audio_file: str
    cues: tuple[Cue, ...]


def _latest_scenario(output_dir: Path) -> Path:
    matches = sorted(
        output_dir.glob("*/scenario.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        raise FileNotFoundError(f"scenario.json을 찾을 수 없습니다: {output_dir}")
    return matches[0]


def _bullet_map(scene: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in scene.get("bullets") or []:
        label, separator, value = str(raw).partition(" · ")
        if separator:
            result[label.strip()] = value.strip()
    return result


def _presentation_narration(scene: dict[str, Any], signal_index: int) -> str:
    kind = str(scene.get("kind") or "")
    if kind != "consensus":
        return str(scene.get("narration") or scene.get("body") or "").strip()

    bullets = _bullet_map(scene)
    title = re.sub(r"^SIGNAL\s+\d+\s*·\s*", "", str(scene.get("title") or ""))
    parts = [f"{signal_index}번 신호는 {title}입니다."]
    if bullets.get("판단"):
        parts.append(bullets["판단"])
    if bullets.get("근거"):
        parts.append(f"분석 기준은 {bullets['근거']}입니다.")
    if bullets.get("분포"):
        parts.append(f"분포는 {bullets['분포']}입니다.")
    if bullets.get("체크"):
        parts.append(f"의사결정 포인트입니다. {bullets['체크']}")
    return " ".join(parts)


def _vtt_seconds(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_vtt(text: str) -> tuple[Cue, ...]:
    cues: list[Cue] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if " --> " in line), None)
        if timing_index is None:
            continue
        start_raw, end_raw = lines[timing_index].split(" --> ", 1)
        caption = " ".join(lines[timing_index + 1 :]).strip()
        if not caption:
            continue
        start = _vtt_seconds(start_raw.split()[0])
        end = _vtt_seconds(end_raw.split()[0])
        if end > start:
            cues.append(Cue(start=start, end=end, text=caption))
    return tuple(cues)


def _metric_values(scene: dict[str, Any]) -> tuple[str, str, int, int, int]:
    bullets = _bullet_map(scene)
    evidence = bullets.get("근거", "데이터 집계 중")
    distribution = bullets.get("분포", "강한 합의 0 / 경합 0")
    event_match = re.search(r"([\d,]+)\s*EVENT", evidence, re.IGNORECASE)
    volume_match = re.search(r"24H\s+([^/]+)$", evidence, re.IGNORECASE)
    strong_match = re.search(r"강한 합의\s*([\d,]+)", distribution)
    contested_match = re.search(r"경합\s*([\d,]+)", distribution)
    event_count = int(event_match.group(1).replace(",", "")) if event_match else 0
    strong = int(strong_match.group(1).replace(",", "")) if strong_match else 0
    contested = int(contested_match.group(1).replace(",", "")) if contested_match else 0
    volume = volume_match.group(1).strip() if volume_match else "-"
    return f"{event_count:,}", volume, event_count, strong, contested


def _scene_html(scene: dict[str, Any], timed: TimedScene, index: int, total: int) -> str:
    kind = str(scene.get("kind") or "consensus")
    title = html.escape(str(scene.get("title") or ""))
    kicker = html.escape(str(scene.get("kicker") or ""))
    start = f"{timed.start:.3f}"
    duration = f"{timed.duration:.3f}"
    accent = {"red": "#ff765f", "blue": "#6ea8ff", "gold": "#e7bb62"}.get(
        str(scene.get("accent") or "gold"), "#e7bb62"
    )
    progress = round(index / total * 100, 2)

    if kind == "intro":
        body = "".join(
            f'<span class="chip reveal">{html.escape(str(item))}</span>'
            for item in (scene.get("bullets") or [])[:3]
        )
        content = f"""
          <div class="intro-lockup">
            <p class="eyebrow reveal">{kicker}</p>
            <h1 class="hero-title reveal">{title}</h1>
            <div class="hero-stat reveal"><strong>22,898</strong><span>OPEN EVENTS</span></div>
            <div class="chip-row">{body}</div>
          </div>"""
    elif kind == "outro":
        checks = "".join(
            f'<li class="reveal"><span>{i:02d}</span>{html.escape(str(item))}</li>'
            for i, item in enumerate((scene.get("bullets") or [])[:3], start=1)
        )
        content = f"""
          <div class="outro-grid">
            <p class="eyebrow reveal">{kicker}</p>
            <h1 class="hero-title reveal">{title}</h1>
            <ol class="check-list">{checks}</ol>
            <p class="disclaimer reveal">예측시장 가격 기반 · 사실 확정 및 투자 조언 아님</p>
          </div>"""
    else:
        bullets = _bullet_map(scene)
        events_text, volume, event_count, strong, contested = _metric_values(scene)
        strong_width = max(4, min(100, round(strong / max(1, event_count) * 100)))
        contested_width = max(4, min(100, round(contested / max(1, event_count) * 100)))
        content = f"""
          <div class="signal-layout">
            <div class="signal-head">
              <p class="eyebrow reveal">{kicker}</p>
              <h1 class="signal-title reveal">{title}</h1>
            </div>
            <div class="decision reveal">
              <span>판단</span>
              <p>{html.escape(bullets.get('판단', '시장 기대를 점검하십시오'))}</p>
            </div>
            <div class="metric-grid">
              <div class="metric reveal"><span>분석 표본</span><strong>{events_text}</strong><em>EVENT</em></div>
              <div class="metric reveal"><span>24시간 거래</span><strong>{html.escape(volume)}</strong><em>VOLUME</em></div>
            </div>
            <div class="distribution reveal">
              <div class="bar-row"><span>강한 합의</span><div class="bar"><i style="--bar:{strong_width}%"></i></div><strong>{strong}</strong></div>
              <div class="bar-row"><span>경합</span><div class="bar"><i style="--bar:{contested_width}%"></i></div><strong>{contested}</strong></div>
            </div>
            <div class="action reveal"><span>EXECUTIVE CHECK</span><p>{html.escape(bullets.get('체크', '변화가 사업 가정에 미치는 영향을 확인하십시오'))}</p></div>
          </div>"""

    return f"""
      <section id="scene-{index}" class="clip scene scene-{kind}" data-start="{start}" data-duration="{duration}" data-track-index="1" style="--accent:{accent}">
        <div class="scene-shell">
          <div class="scene-grid"></div>
          <div class="corner corner-a"></div><div class="corner corner-b"></div>
          <header><span>NUNCHI / POLYMARKET</span><span>{index:02d} / {total:02d}</span></header>
          {content}
          <footer><span>CONSENSUS INTELLIGENCE</span><div class="progress"><i style="width:{progress}%"></i></div></footer>
        </div>
      </section>"""


def _caption_html(timed_scenes: list[TimedScene]) -> str:
    rows: list[str] = []
    cue_index = 0
    for timed in timed_scenes:
        for cue in timed.cues:
            cue_index += 1
            start = timed.start + cue.start
            duration = max(0.08, cue.end - cue.start)
            rows.append(
                f'<div id="caption-{cue_index}" class="clip caption" '
                f'data-start="{start:.3f}" data-duration="{duration:.3f}" data-track-index="30">'
                f'<span>{html.escape(cue.text)}</span></div>'
            )
    return "\n".join(rows)


def _index_html(
    scenario: dict[str, Any], timed_scenes: list[TimedScene], total_duration: float
) -> str:
    scenes = scenario.get("scenes") or []
    scene_markup = "\n".join(
        _scene_html(scene, timed_scenes[index], index + 1, len(scenes))
        for index, scene in enumerate(scenes)
    )
    audio_markup = "\n".join(
        f'<audio id="voice-{index}" src="{html.escape(timed.audio_file)}" '
        f'data-start="{timed.start:.3f}" data-duration="{timed.duration:.3f}" '
        f'data-track-index="20" data-volume="1" preload="auto"></audio>'
        for index, timed in enumerate(timed_scenes, start=1)
    )
    scene_specs = json.dumps(
        [
            {"id": f"scene-{index}", "start": timed.start, "duration": timed.duration}
            for index, timed in enumerate(timed_scenes, start=1)
        ],
        ensure_ascii=False,
    )
    caption_markup = _caption_html(timed_scenes)
    date_text = html.escape(str(scenario.get("date") or ""))
    return f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <title>Polymarket Daily Executive Brief</title>
    <script src="node_modules/gsap/dist/gsap.min.js"></script>
    <style>
      * {{ box-sizing:border-box; }}
      html,body {{ margin:0; width:1080px; height:1920px; overflow:hidden; background:#0c1110; }}
      body {{ color:#f4f0e7; font-family:"Noto Sans KR","Malgun Gothic",system-ui,sans-serif; }}
      #root {{ position:relative; width:1080px; height:1920px; overflow:hidden; }}
      .clip {{ position:absolute; inset:0; width:1080px; height:1920px; }}
      .base-ground {{ background:#0c1110; overflow:hidden; }}
      .base-ground::before {{ content:""; position:absolute; inset:-300px; background:radial-gradient(circle at 78% 18%,rgba(231,187,98,.17),transparent 32%),radial-gradient(circle at 15% 82%,rgba(110,168,255,.12),transparent 35%); }}
      .scene-shell {{ position:absolute; inset:0; padding:72px 72px 92px; overflow:hidden; background:radial-gradient(circle at 88% 28%,color-mix(in srgb,var(--accent) 18%,transparent),transparent 31%); }}
      .scene-grid {{ position:absolute; inset:0; opacity:.16; background-image:linear-gradient(rgba(244,240,231,.16) 2px,transparent 2px),linear-gradient(90deg,rgba(244,240,231,.16) 2px,transparent 2px); background-size:90px 90px; mask-image:linear-gradient(to bottom,black,transparent 74%); }}
      header,footer {{ position:relative; z-index:2; display:flex; justify-content:space-between; align-items:center; font:700 22px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace; letter-spacing:.11em; color:#a9afa7; }}
      footer {{ position:absolute; left:72px; right:72px; bottom:66px; gap:28px; }}
      .progress {{ flex:1; height:6px; background:#2b322f; overflow:hidden; }}
      .progress i {{ display:block; height:100%; background:var(--accent); }}
      .corner {{ position:absolute; width:150px; height:150px; border-color:var(--accent); opacity:.55; }}
      .corner-a {{ left:38px; top:38px; border-left:3px solid var(--accent); border-top:3px solid var(--accent); }}
      .corner-b {{ right:38px; bottom:38px; border-right:3px solid var(--accent); border-bottom:3px solid var(--accent); }}
      .eyebrow {{ margin:0 0 30px; color:var(--accent); font:800 24px/1.3 ui-monospace,SFMono-Regular,Consolas,monospace; letter-spacing:.13em; }}
      .hero-title {{ margin:0; width:900px; font-size:94px; line-height:1.08; letter-spacing:-.055em; font-weight:900; }}
      .intro-lockup {{ position:relative; z-index:2; margin-top:290px; }}
      .hero-stat {{ margin-top:120px; display:flex; align-items:flex-end; gap:24px; border-bottom:4px solid var(--accent); padding-bottom:30px; width:900px; }}
      .hero-stat strong {{ font:900 150px/.85 ui-monospace,SFMono-Regular,Consolas,monospace; color:var(--accent); letter-spacing:-.08em; }}
      .hero-stat span {{ font:700 24px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace; color:#a9afa7; padding-bottom:10px; }}
      .chip-row {{ margin-top:74px; display:flex; flex-wrap:wrap; gap:18px; }}
      .chip {{ padding:18px 25px; border:2px solid #52605a; color:#d8ded9; font-size:28px; font-weight:700; background:#17201d; }}
      .signal-layout {{ position:relative; z-index:2; margin-top:120px; }}
      .signal-title {{ margin:0; max-width:920px; font-size:72px; line-height:1.12; letter-spacing:-.045em; font-weight:900; }}
      .decision {{ margin-top:70px; padding:38px 40px 42px; border-top:4px solid var(--accent); background:#17201d; }}
      .decision span,.action span,.metric span {{ display:block; margin-bottom:15px; color:var(--accent); font:800 21px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace; letter-spacing:.11em; }}
      .decision p {{ margin:0; font-size:45px; line-height:1.35; font-weight:800; letter-spacing:-.025em; }}
      .metric-grid {{ margin-top:38px; display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
      .metric {{ min-height:240px; padding:30px; border:2px solid #3e4a45; background:#101715; }}
      .metric strong {{ display:block; margin-top:30px; color:#f4f0e7; font:900 68px/1 ui-monospace,SFMono-Regular,Consolas,monospace; letter-spacing:-.06em; }}
      .metric em {{ display:block; margin-top:14px; color:#89938e; font:700 18px/1 ui-monospace,SFMono-Regular,Consolas,monospace; font-style:normal; }}
      .distribution {{ margin-top:36px; padding:34px; border:2px solid #3e4a45; }}
      .bar-row {{ display:grid; grid-template-columns:150px 1fr 55px; gap:20px; align-items:center; margin:22px 0; font-size:25px; font-weight:700; }}
      .bar {{ height:15px; background:#28312d; overflow:hidden; }}
      .bar i {{ display:block; width:var(--bar); height:100%; background:var(--accent); transform-origin:left center; }}
      .bar-row strong {{ text-align:right; font:900 30px/1 ui-monospace,SFMono-Regular,Consolas,monospace; }}
      .action {{ margin-top:38px; padding:34px 38px; background:var(--accent); color:#0c1110; }}
      .action span {{ color:#0c1110; opacity:.72; }}
      .action p {{ margin:0; font-size:36px; line-height:1.38; font-weight:900; letter-spacing:-.025em; }}
      .outro-grid {{ position:relative; z-index:2; margin-top:245px; }}
      .check-list {{ margin:110px 0 0; padding:0; list-style:none; border-top:3px solid #44504b; }}
      .check-list li {{ display:grid; grid-template-columns:92px 1fr; align-items:center; min-height:150px; border-bottom:2px solid #38413d; font-size:39px; font-weight:800; }}
      .check-list li span {{ color:var(--accent); font:800 25px/1 ui-monospace,SFMono-Regular,Consolas,monospace; }}
      .disclaimer {{ margin-top:70px; color:#a9afa7; font-size:25px; line-height:1.5; }}
      .caption {{ display:flex; align-items:flex-end; justify-content:center; padding:0 74px 172px; pointer-events:none; }}
      .caption span {{ max-width:930px; padding:18px 28px 20px; color:#f7f4ed; background:rgba(8,12,11,.90); border:2px solid rgba(244,240,231,.24); font-size:35px; line-height:1.42; font-weight:750; text-align:center; box-shadow:0 14px 40px rgba(0,0,0,.28); }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-width="1080" data-height="1920" data-duration="{total_duration:.3f}">
      <div id="base-ground" class="clip base-ground" data-start="0" data-duration="{total_duration:.3f}" data-track-index="0"></div>
      {scene_markup}
      {caption_markup}
      {audio_markup}
      <div style="display:none" aria-hidden="true">source {date_text}</div>
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused:true }});
      const scenes = {scene_specs};
      scenes.forEach((scene) => {{
        const entry = scene.start + 0.08;
        const exit = Math.max(entry + 0.8, scene.start + scene.duration - 0.42);
        tl.fromTo(`#${{scene.id}} .reveal`,
          {{ x:120, autoAlpha:0 }},
          {{ x:0, autoAlpha:1, duration:0.58, stagger:0.09, ease:"power4.out", immediateRender:false }}, entry);
        tl.fromTo(`#${{scene.id}} .bar i`,
          {{ scaleX:0 }},
          {{ scaleX:1, duration:0.72, stagger:0.12, ease:"power3.out", immediateRender:false }}, entry + 0.8);
        tl.to(`#${{scene.id}} .scene-shell`, {{ x:-120, duration:0.42, ease:"power4.in" }}, exit);
      }});
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def export_project(
    scenario_path: Path,
    project_dir: Path,
    *,
    settings: Settings,
    voice: str | None = None,
    rate: str | None = None,
) -> dict[str, Any]:
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    scenes = scenario.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("scenario.json에 scenes가 없습니다")

    project_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    timed_scenes: list[TimedScene] = []
    cursor = 0.0
    signal_index = 0
    for index, scene in enumerate(scenes, start=1):
        if str(scene.get("kind") or "") == "consensus":
            signal_index += 1
        narration = _presentation_narration(scene, signal_index)
        audio_path = assets_dir / f"voice-{index:02d}.mp3"
        subtitle_path = assets_dir / f"voice-{index:02d}.vtt"
        synthesize(
            narration,
            audio_path=audio_path,
            subtitle_path=subtitle_path,
            voice=voice or settings.tts_voice,
            rate=rate or settings.tts_rate,
        )
        duration = probe_duration(audio_path, ffprobe_bin=settings.ffprobe_bin)
        cues = parse_vtt(subtitle_path.read_text(encoding="utf-8-sig"))
        timed_scenes.append(
            TimedScene(
                start=cursor,
                duration=duration,
                audio_file=f"assets/{audio_path.name}",
                cues=cues,
            )
        )
        cursor += duration

    if cursor > settings.max_duration_seconds:
        raise ValueError(
            f"HyperFrames 내레이션이 {cursor:.1f}초로 제한 {settings.max_duration_seconds:.1f}초를 초과합니다"
        )

    (project_dir / "index.html").write_text(
        _index_html(scenario, timed_scenes, cursor), encoding="utf-8"
    )
    shutil.copy2(scenario_path, project_dir / "source-scenario.json")
    manifest = {
        "date": scenario.get("date"),
        "source": str(scenario_path.resolve()),
        "duration_seconds": round(cursor, 3),
        "voice": voice or settings.tts_voice,
        "rate": rate or settings.tts_rate,
        "scenes": [
            {
                "start": round(timed.start, 3),
                "duration": round(timed.duration, 3),
                "audio": timed.audio_file,
            }
            for timed in timed_scenes
        ],
    }
    (project_dir / "source-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polymarket scenario.json을 HyperFrames 세로 영상 프로젝트로 변환"
    )
    parser.add_argument("--scenario", type=Path, help="입력 scenario.json; 생략하면 최신 출력 사용")
    parser.add_argument(
        "--project",
        type=Path,
        default=PROJECT_DIR / "videos" / "consensus-demo",
        help="HyperFrames 프로젝트 디렉터리",
    )
    parser.add_argument("--voice", help="Edge-TTS 음성 ID")
    parser.add_argument("--rate", help="Edge-TTS 속도, 예: +4%%")
    args = parser.parse_args()
    settings = Settings.from_env()
    scenario_path = args.scenario or _latest_scenario(settings.output_dir)
    manifest = export_project(
        scenario_path.resolve(),
        args.project.resolve(),
        settings=settings,
        voice=args.voice,
        rate=args.rate,
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
