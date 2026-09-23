import asyncio
from pathlib import Path

from services.telegram_bot.state.sent_tracker import SentNewsTracker


def test_empty_history_file_starts_with_empty_tracker(tmp_path: Path) -> None:
    history_file = tmp_path / "sent_ids.json"
    history_file.write_text("", encoding="utf-8")

    tracker = SentNewsTracker(history_file)

    assert asyncio.run(tracker.reserve("article-1")) is True


def test_invalid_history_file_starts_with_empty_tracker(tmp_path: Path) -> None:
    history_file = tmp_path / "sent_ids.json"
    history_file.write_text("not json", encoding="utf-8")

    tracker = SentNewsTracker(history_file)

    assert asyncio.run(tracker.reserve("article-1")) is True
