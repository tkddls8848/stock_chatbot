from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
import logging

from .config import Settings
from .pipeline import produce_daily


def main() -> None:
    parser = argparse.ArgumentParser(description="하루 한 편 Polymarket 컨센서스 쇼츠 제작")
    parser.add_argument("--date", help="제작일 YYYY-MM-DD, 기본값은 한국 날짜")
    parser.add_argument("--force", action="store_true", help="오늘 산출물이 있어도 다시 제작")
    parser.add_argument("--upload", action="store_true", help="환경 설정과 무관하게 업로드")
    parser.add_argument("--no-upload", action="store_true", help="환경 설정과 무관하게 업로드 안 함")
    args = parser.parse_args()
    if args.upload and args.no_upload:
        parser.error("--upload와 --no-upload는 함께 쓸 수 없습니다")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    upload = True if args.upload else False if args.no_upload else None
    result = produce_daily(
        settings,
        production_date=date.fromisoformat(args.date) if args.date else None,
        force=args.force,
        upload=upload,
    )
    print(json.dumps(asdict(result), ensure_ascii=False))


if __name__ == "__main__":
    main()

