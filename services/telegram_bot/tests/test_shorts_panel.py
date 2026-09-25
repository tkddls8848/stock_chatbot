"""텔레그램 `/shorts`: 쇼츠 CLI를 하위 프로세스로 부르는 관리 패널.

쇼츠 패키지는 import하지 않는다. 계약은 CLI 명령과 stdout JSON이라 여기서는 가짜
실행기(`FakeRunner`)와, 실제 하위 프로세스 경계를 흉내 내는 가짜 파이썬 스크립트로 본다.
"""

import asyncio

import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.telegram_bot.core.clock import JST
from services.telegram_bot.core.config import BASE_DIR, SHORTS_SCHEDULE_HOUR
from services.telegram_bot.features.shorts import handlers
from services.telegram_bot.features.shorts.runner import ShortsError, ShortsRunner

STATUS = {"state": "ok", "date": "2026-09-24", "review_status": "pending", "revision": False,
          "duration_seconds": 61.2, "video_path": None, "video_bytes": None,
          "metadata": {"title": "제목 <b>", "description": "설명", "tags": ["집단 예측"]}}


class FakeRunner:
    def __init__(self, responses=None, error=None):
        self.calls, self.responses, self.error = [], responses or {}, error

    async def call(self, args, *, timeout):
        self.calls.append(args)
        if self.error:
            raise self.error
        key = args[0] if args else "run"
        return dict(self.responses.get(key, STATUS))


class Message:
    def __init__(self):
        self.texts, self.videos = [], []

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)

    async def reply_video(self, stream, **kwargs):
        self.videos.append((stream.read(), kwargs))


def _run(args, runner):
    message = Message()
    context = SimpleNamespace(args=args, bot_data={"shorts_runner": runner})
    update = SimpleNamespace(effective_message=message)

    async def go():
        await handlers.cmd_shorts(update, context)
        await asyncio.gather(*context.bot_data.get("shorts_tasks", set()))

    asyncio.run(go())
    return message, context


def test_status_escapes_titles_and_shows_the_next_schedule():
    message, _ = _run([], FakeRunner())
    text = message.texts[0]
    assert "검수 대기" in text and "제목 &lt;b&gt;" in text and "다음 예약 제작" in text


def test_next_schedule_rolls_to_tomorrow_after_the_slot():
    before = datetime(2026, 9, 24, 20, 0, tzinfo=JST)
    after = datetime(2026, 9, 24, 21, 30, tzinfo=JST)
    assert handlers.next_schedule_text(before) == "09-24 21:00"
    assert handlers.next_schedule_text(after) == "09-25 21:00"


def test_schedule_hour_matches_the_systemd_timer():
    timer = (BASE_DIR / "infra" / "systemd" / "polymarket-shorts.timer").read_text(encoding="utf-8")
    hour = int(re.search(r"OnCalendar=\S+ (\d{2}):00:00", timer).group(1))
    assert hour == SHORTS_SCHEDULE_HOUR


def test_run_and_edit_go_through_the_cli_in_the_background():
    runner = FakeRunner({"--edit": {**STATUS, "summary": "첫 멘트를 질문형으로"}})
    message, _ = _run(["run", "force"], runner)
    assert runner.calls == [["--force"], ["--status"]]
    assert "시작했습니다" in message.texts[0] and "끝." in message.texts[1]
    runner = FakeRunner({"--edit": {**STATUS, "summary": "첫 멘트를 질문형으로"}})
    message, _ = _run(["edit", "첫", "멘트를", "질문형으로"], runner)
    assert runner.calls[0] == ["--edit", "첫 멘트를 질문형으로"]
    assert "첫 멘트를 질문형으로" in message.texts[1]


def test_second_job_waits_instead_of_overlapping():
    message = Message()
    lock = asyncio.Lock()
    context = SimpleNamespace(args=["run"], bot_data={"shorts_runner": FakeRunner(), "shorts_lock": lock})

    async def go():
        async with lock:
            await handlers.cmd_shorts(SimpleNamespace(effective_message=message), context)

    asyncio.run(go())
    assert "이미 돌고 있습니다" in message.texts[0]


def test_preview_sends_the_video_and_metadata(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4")
    runner = FakeRunner({"--status": {**STATUS, "video_path": str(video), "video_bytes": 3}})
    message, _ = _run(["preview"], runner)
    assert message.videos[0][0] == b"mp4"
    assert "설명" in message.texts[-1] and "집단 예측" in message.texts[-1]


def test_oversized_video_is_not_uploaded(tmp_path):
    runner = FakeRunner({"--status": {**STATUS, "video_path": "/x.mp4", "video_bytes": 60 * 1024 * 1024}})
    message, _ = _run(["preview"], runner)
    assert not message.videos and "50MB" in message.texts[0]


def test_cli_errors_are_reported_not_raised():
    message, _ = _run([], FakeRunner(error=ShortsError("쇼츠 venv가 없습니다: /nope")))
    assert message.texts == ["쇼츠: 쇼츠 venv가 없습니다: /nope"]


def _fake_python(tmp_path, body):
    package = tmp_path / "polymarket_shorts"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8", newline="\n")
    (package / "cli.py").write_text(body, encoding="utf-8", newline="\n")
    return sys.executable


def test_runner_passes_only_a_minimal_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    python = _fake_python(tmp_path, 'import json, os, sys\n'
                         'print(json.dumps({"token": os.getenv("TELEGRAM_BOT_TOKEN", ""), '
                         '"storage": os.getenv("STORAGE_DIR"), "args": sys.argv[1:]}))\n')
    runner = ShortsRunner(python=python, workdir=str(tmp_path), storage_dir="/srv/storage")
    payload = asyncio.run(runner.call(["--status"], timeout=10))
    assert payload == {"token": "", "storage": "/srv/storage", "args": ["--status"]}


def test_runner_turns_failures_into_short_errors(tmp_path):
    python = _fake_python(tmp_path, 'import sys\n'
                         'sys.stderr.buffer.write("Traceback\\n검수할 영상이 없습니다\\n".encode("utf-8"))\n'
                         'sys.exit(1)\n')
    with pytest.raises(ShortsError, match="검수할 영상이 없습니다"):
        asyncio.run(ShortsRunner(python=python, workdir=str(tmp_path)).call(["--complete"], timeout=10))
    with pytest.raises(ShortsError, match="venv가 없습니다"):
        asyncio.run(ShortsRunner(python=str(Path(tmp_path) / "missing")).call([], timeout=1))
    slow = _fake_python(tmp_path, "import time\ntime.sleep(5)\n")
    with pytest.raises(ShortsError, match="끝나지 않아"):
        asyncio.run(ShortsRunner(python=slow, workdir=str(tmp_path)).call([], timeout=0.2))


def test_web_admin_hub_opens_shorts():
    from services.telegram_bot.features import ALL_FEATURES, build_feature_registry
    from services.telegram_bot.handlers.menus import web_admin_menu

    registry = build_feature_registry(feature.key for feature in ALL_FEATURES)
    buttons = [b.callback_data for row in web_admin_menu(registry).inline_keyboard for b in row]
    assert "nav:shorts" in buttons


def test_runner_cancellation_kills_and_reaps_the_child(monkeypatch):
    process = SimpleNamespace(returncode=None, killed=False, reaped=False)

    async def exercise():
        started = asyncio.Event()

        async def communicate():
            started.set()
            await asyncio.Event().wait()

        def kill():
            process.killed = True
            process.returncode = -9

        async def wait():
            process.reaped = True

        async def spawn(*args, **kwargs):
            return process

        process.communicate, process.kill, process.wait = communicate, kill, wait
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(ShortsRunner(python=sys.executable).call([], timeout=60))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert process.killed and process.reaped
