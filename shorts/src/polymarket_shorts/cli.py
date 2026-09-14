from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
import logging
from pathlib import Path

from .config import Settings
from .pipeline import produce_daily, produce_editorial
from .review import ReviewError, read_script


_GATES = ("plan", "review", "workflow", "browser")


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
    args = parser.parse_args()
    chosen = [name for name in _GATES if getattr(args, name)]
    if len(chosen) > 1:
        parser.error("--plan, --review, --workflow는 한 번에 하나만 씁니다")
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
            payload = asdict(produce_daily(
                settings,
                production_date=date.fromisoformat(args.date) if args.date else None,
                force=args.force,
            ))
        if args.interactive and payload.get("video_path"):
            from .workflow import interact
            interact(Path(payload["video_path"]).parent, settings)
            return
    except ReviewError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
