from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime
import json
import logging
from pathlib import Path

from .config import Settings
from .pipeline import produce_daily, produce_editorial, prune_old_days
from .review import ReviewError, read_script


_GATES = ("plan", "review", "workflow", "browser", "status", "edit", "complete", "upload", "youtube_auth")


def main() -> None:
    parser = argparse.ArgumentParser(description="하루 한 편 Polymarket 컨센서스 쇼츠 제작")
    parser.add_argument("--date", help="제작일 YYYY-MM-DD, 기본값은 한국 날짜")
    parser.add_argument("--force", action="store_true", help="오늘 산출물이 있어도 다시 제작")
    parser.add_argument("--plan", type=Path, help="정제·검수된 editorial.json을 재요약 없이 영상화")
    parser.add_argument("--review", type=Path, metavar="DIR", help="산출물 폴더의 검수 원고를 출력")
    parser.add_argument("--workflow", type=Path, metavar="DIR", help="기존 산출물을 자연어로 검수하고 수정")
    parser.add_argument("--browser", type=Path, metavar="DIR", help="기존 산출물을 로컬 브라우저 패널에서 검수")
    parser.add_argument("--port", type=int, default=8765, help="브라우저 패널 포트, 기본값 8765")
    parser.add_argument("--interactive", action="store_true", help="영상 생성 후 대화형 검수·편집 시작")
    # 텔레그램 관리 패널(/shorts)이 하위 프로세스로 부르는 비대화형 명령. stdout에 JSON 한 줄.
    parser.add_argument("--status", action="store_true", help="최근 제작일의 제작·검수 상태 JSON")
    parser.add_argument("--edit", metavar="TEXT", help="최근 제작일의 현재 수정본을 자연어로 고치고 다시 렌더")
    parser.add_argument("--complete", action="store_true", help="최근 제작일의 현재 수정본을 검수 완료로 기록")
    parser.add_argument("--upload", nargs="?", const="latest", metavar="DIR", help="검수 완료된 현재 수정본 업로드")
    parser.add_argument("--youtube-auth", action="store_true", help="운영자 PC에서 최초 YouTube 승인")
    args = parser.parse_args()
    chosen = [name for name in _GATES if getattr(args, name)]
    if len(chosen) > 1:
        parser.error("--plan, --review, --workflow, --browser, --status, --edit, --complete, --upload, --youtube-auth는 한 번에 하나만 씁니다")
    if chosen and (args.date or args.force):
        parser.error(f"--{chosen[0]}은 --date, --force와 함께 쓸 수 없습니다")
    if args.interactive and chosen and chosen != ["plan"]:
        parser.error("--interactive는 일일 제작 또는 --plan과 함께 씁니다")
    if not 1 <= args.port <= 65535:
        parser.error("--port는 1부터 65535까지입니다")
    if not args.browser and args.port != 8765:
        parser.error("--port는 --browser와 함께 씁니다")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    try:
        if args.youtube_auth:
            from .youtube import authorize
            print("SHORTS_YOUTUBE_REFRESH_TOKEN=" + authorize(settings))
            return
        if args.upload:
            from .status import latest_root
            from .youtube import upload
            root = latest_root(settings) if args.upload == "latest" else Path(args.upload).resolve()
            payload = upload(root, settings) if root else {"status": "not_reviewed", "video_id": None, "url": None}
            print(json.dumps(payload, ensure_ascii=False))
            return
        if args.status or args.edit is not None or args.complete:
            print(json.dumps(_panel(args, settings), ensure_ascii=False))
            return
        if args.workflow:
            from .workflow import interact
            interact(args.workflow, settings)
            return
        if args.browser:
            from .panel import serve
            serve(args.browser, settings, port=args.port)
            return
        if args.review:
            print(read_script(args.review.resolve()))
            return
        if args.plan:
            payload = asdict(produce_editorial(args.plan.resolve(), settings))
        else:
            # 새로 렌더하기 전에 보관 기간이 지난 제작일 폴더부터 비워 디스크 여유를 만든다.
            prune_old_days(settings, datetime.now(settings.timezone).date())
            payload = asdict(produce_daily(
                settings,
                production_date=date.fromisoformat(args.date) if args.date else None,
                force=args.force,
            ))
            if settings.auto_publish and not args.interactive:
                payload["upload"] = _auto_publish(payload, settings)
            if settings.english_edition and not args.interactive:
                payload["english"] = _english(payload, settings, force=args.force)
        if args.interactive and payload.get("video_path"):
            from .workflow import interact
            interact(Path(payload["video_path"]).parent, settings)
            return
    except ReviewError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, ensure_ascii=False))


def _english(payload: dict, settings: Settings, *, force: bool) -> dict | None:
    """한국어판을 만든(또는 이미 있는) 날에 영어판을 만들고, 자동 업로드면 올린다.

    한국어판 업로드가 끝난 뒤에 돈다 — 영어판 실패가 한국어판 게시를 막지 않는다.
    """
    from .english import english_root, produce_english

    if payload.get("status") not in {"pending_review", "already_produced"}:
        return None
    result = asdict(produce_english(settings, date.fromisoformat(payload["date"]), force=force))
    if settings.auto_publish:
        result["upload"] = _auto_publish(result, settings, root=english_root(settings, payload["date"]))
    return result


def _auto_publish(payload: dict, settings: Settings, *, root: Path | None = None) -> dict | None:
    """제작 결과를 검수 없이 바로 올린다. 그날 이미 올린 영상이 있으면 건너뛴다.

    업로드 오류는 ReviewError로 올라가 서비스가 실패하고, timer의 재시도는
    already_produced를 거쳐 여기로 다시 온다(아직 안 올렸으므로 다시 시도한다).
    """
    from .review import complete_review, operation_lock
    from .workflow import current_target
    from .youtube import upload

    if payload.get("status") not in {"pending_review", "already_produced"}:
        return None
    root = root or settings.output_dir / payload["date"]
    if not (root / "review.json").is_file() and not (root / "workflow.json").is_file():
        return None
    for record in root.rglob("upload.json"):
        revisions = json.loads(record.read_text(encoding="utf-8")).get("revisions", {})
        if any(entry.get("video_id") for entry in revisions.values()):
            return {"status": "day_already_uploaded"}
    with operation_lock(root, ".workflow.lock"):
        complete_review(current_target(root))
    result = upload(root, settings)
    logging.getLogger(__name__).info("자동 업로드: %s", result)
    return result


def _panel(args, settings: Settings) -> dict:
    from .review import complete_review, operation_lock
    from .status import current_status, latest_root
    from .workflow import current_target, revise

    if args.status:
        return current_status(settings)
    root = latest_root(settings)
    if root is None or not ((root / "review.json").is_file() or (root / "workflow.json").is_file()):
        raise ReviewError("검수할 영상이 없습니다. 먼저 제작하세요")
    if args.complete:
        with operation_lock(root, ".workflow.lock"):
            complete_review(current_target(root))
            return current_status(settings, root=root)
    _, summary = revise(root, args.edit, settings)
    return {**current_status(settings), "summary": summary}


if __name__ == "__main__":
    main()
