"""원고 전체 전달·승인 시계·버전별 제어의 봇/CLI 계약."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from services.telegram_bot.features.shorts import handlers, review
from services.telegram_bot.features.shorts.feature import FEATURE
from services.telegram_bot.features.shorts.runner import ShortsError
from services.telegram_bot.tests.test_shorts_panel import FakeRunner, Message, STATUS, _run

TOKEN = "2026-10-06-en-" + "a" * 32
ITEM = {"token": TOKEN, "script": "시나리오 " * 1500, "state": "pending",
        "date": "2026-10-06", "language": "en", "delivered_at": None}


def _app(monkeypatch, item=ITEM, send=None):
    monkeypatch.setattr(review, "TELEGRAM_CHAT_ID", "123")
    runner = FakeRunner({"--review-pending": {"items": [item]}, "--review-tick": {"items": []}})
    return SimpleNamespace(bot_data={"shorts_runner": runner},
                           bot=SimpleNamespace(send_message=send or AsyncMock())), runner


def test_complete_scenario_delivered_in_bounded_chunks_before_ack(monkeypatch):
    app, runner = _app(monkeypatch)

    async def check_send(**kwargs):
        assert ["--review-ack", TOKEN] not in runner.calls
        assert len(kwargs["text"]) <= 3500

    app.bot.send_message.side_effect = check_send
    asyncio.run(review.poll_reviews(app))
    messages = app.bot.send_message.call_args_list
    assert len(messages) >= 3
    assert ITEM["script"] in "".join(call.kwargs["text"] for call in messages)
    assert runner.calls == [["--review-pending"], ["--review-ack", TOKEN], ["--review-tick"]]
    buttons = messages[-1].kwargs["reply_markup"].inline_keyboard[0]
    assert all(len(b.callback_data.encode()) <= 64 for b in buttons)


def test_partial_delivery_failure_never_acknowledges(monkeypatch):
    send = AsyncMock(side_effect=[None, RuntimeError("telegram unavailable")])
    app, runner = _app(monkeypatch, send=send)
    asyncio.run(review.poll_reviews(app))
    assert ["--review-ack", TOKEN] not in runner.calls


def test_delivered_or_paused_scenario_not_repeated_by_poll(monkeypatch):
    for item in ({**ITEM, "delivered_at": "2026-10-06T10:00:00+09:00"}, {**ITEM, "state": "paused"}):
        app, runner = _app(monkeypatch, item)
        asyncio.run(review.poll_reviews(app))
        app.bot.send_message.assert_not_called()
        assert runner.calls == [["--review-pending"], ["--review-tick"]]


def test_repeated_upload_failure_notifies_once(monkeypatch):
    app, runner = _app(monkeypatch, {**ITEM, "state": "paused"})
    runner.responses["--review-tick"] = {"items": [{"token": TOKEN, "status": "no_credentials"}]}

    async def go():
        await review.poll_reviews(app)
        await review.poll_reviews(app)

    asyncio.run(go())
    app.bot.send_message.assert_called_once()


def test_poll_does_not_compete_with_running_edit(monkeypatch):
    app, runner = _app(monkeypatch)

    async def go():
        async with review.lock(app):
            await review.poll_reviews(app)

    asyncio.run(go())
    assert runner.calls == []


def test_scheduler_and_callback_are_registered():
    scheduler = Mock()
    FEATURE.install_jobs(scheduler, "app")
    assert scheduler.add_job.call_args.kwargs["minutes"] == 1
    assert scheduler.add_job.call_args.kwargs["max_instances"] == 1
    assert FEATURE.callbacks[0].prefixes == ("shr:",)


def test_explicit_english_edit_pauses_before_background_work():
    runner = FakeRunner({"--edit": {"summary": "자료 갱신"}})
    _run(["edit", TOKEN, "자료", "갱신"], runner)
    assert runner.calls[:2] == [["--review-pause", TOKEN], ["--edit", "자료 갱신", "--review-token", TOKEN]]


def test_stale_revision_edit_does_not_launch_or_leak_lock():
    runner = FakeRunner(error=ShortsError("새 수정본으로 대체되었습니다"))
    message, context = _run(["edit", TOKEN, "자료 갱신"], runner)
    assert runner.calls == [["--review-pause", TOKEN]]
    assert "대체" in message.texts[-1]
    assert not review.lock(context).locked()


def test_done_uses_status_token_not_unpinned_legacy_completion():
    runner = FakeRunner({"--status": {**STATUS, "approval": {"token": TOKEN}},
                         "--review-approve": {"status": "uploaded", "url": "https://example.test/video"}})
    message, _ = _run(["done"], runner)
    assert runner.calls == [["--status"], ["--review-approve", TOKEN]]
    assert "업로드 완료" in message.texts[-1]


def test_pause_callback_uses_exact_token_and_surfaces_stale_error():
    runner = FakeRunner(error=ShortsError("이미 바뀐 원고"))
    message = Message()
    context = SimpleNamespace(bot_data={"shorts_runner": runner})
    handled = asyncio.run(handlers.review_callback(SimpleNamespace(message=message), context, f"shr:p:{TOKEN}"))
    assert handled
    assert runner.calls == [["--review-pause", TOKEN]]
    assert "바뀐 원고" in message.texts[-1]


def test_manual_review_of_paused_scenario_does_not_restart_clock():
    runner = FakeRunner({"--review-pending": {"items": [{**ITEM, "state": "paused"}]}})
    message, _ = _run(["review"], runner)
    assert runner.calls == [["--review-pending"]]
    assert "보류되었습니다" in message.texts[0]


def test_english_edit_previews_returned_edition_not_latest_korean(tmp_path):
    video = tmp_path / "english.mp4"
    video.write_bytes(b"english")
    runner = FakeRunner({"--edit": {**STATUS, "video_path": str(video), "video_bytes": 7,
                                    "metadata": {"title": "English edition"}}})
    message, _ = _run(["edit", TOKEN, "자료 갱신"], runner)
    assert ["--status"] not in runner.calls
    assert message.videos[0][0] == b"english"
    assert "English edition" in message.texts[1]


def test_manual_review_surfaces_invalid_gate_without_missing_token_crash():
    runner = FakeRunner({"--review-pending": {"items": [{"state": "error", "date": "2026-10-06",
                                                        "error": "원고 파일 손상"}]}})
    message, _ = _run(["review"], runner)
    assert "원고 파일 손상" in message.texts[0]
    assert runner.calls == [["--review-pending"]]


def test_edit_prompt_pauses_before_accepting_input():
    runner = FakeRunner({"--status": {**STATUS, "approval": {"token": TOKEN}}})
    message = Message()
    context = SimpleNamespace(args=["edit_prompt"], user_data={}, bot_data={"shorts_runner": runner})
    asyncio.run(handlers.cmd_shorts(SimpleNamespace(effective_message=message), context))
    assert runner.calls == [["--status"], ["--review-pause", TOKEN]]
    assert context.user_data == {"menu_input": "shorts_edit", "shorts_review_token": TOKEN}
    assert "보류했습니다" in message.texts[0]


def test_english_edit_button_pauses_and_binds_exact_token():
    runner = FakeRunner()
    message = Message()
    context = SimpleNamespace(user_data={}, bot_data={"shorts_runner": runner})
    asyncio.run(handlers.review_callback(SimpleNamespace(message=message), context, f"shr:e:{TOKEN}"))
    assert runner.calls == [["--review-pause", TOKEN]]
    assert context.user_data["shorts_review_token"] == TOKEN
    assert context.user_data["menu_input"] == "shorts_edit"


def test_failed_edit_button_pause_does_not_accept_input():
    context = SimpleNamespace(user_data={}, bot_data={"shorts_runner": FakeRunner(error=ShortsError("오래된 원고"))})
    message = Message()
    asyncio.run(handlers.review_callback(SimpleNamespace(message=message), context, f"shr:e:{TOKEN}"))
    assert not context.user_data
    assert "오래된 원고" in message.texts[0]


def test_menu_input_forwards_and_clears_bound_review_token(monkeypatch):
    from services.telegram_bot.handlers import navigation

    cmd = AsyncMock()
    monkeypatch.setattr(navigation, "cmd_shorts", cmd)
    message = SimpleNamespace(text="최신 자료로 바꿔줘")
    registry = SimpleNamespace(persistent_callback=lambda text: None)
    context = SimpleNamespace(user_data={"menu_input": "shorts_edit", "shorts_review_token": TOKEN},
                              bot_data={"feature_registry": registry}, application=None)
    asyncio.run(navigation.handle_menu_text(SimpleNamespace(effective_message=message), context))
    assert cmd.call_args.args[1].args == ["edit", TOKEN, "최신", "자료로", "바꿔줘"]
    assert not context.user_data


def test_menu_edit_button_routes_through_pause_prompt(monkeypatch):
    from services.telegram_bot.handlers import navigation

    cmd = AsyncMock()
    monkeypatch.setattr(navigation, "cmd_shorts", cmd)
    monkeypatch.setattr(navigation, "_dispatch_primary_menu_action", AsyncMock(return_value=False))
    registry = SimpleNamespace(menu_owner=lambda value: None)
    context = SimpleNamespace(user_data={}, bot_data={"feature_registry": registry}, application=None)
    message = Message()
    update = SimpleNamespace(effective_message=message, callback_query=SimpleNamespace(message=message))
    asyncio.run(navigation.handle_menu_callback(update, context, "nav:shorts:edit"))
    assert cmd.call_args.args[1].args == ["edit_prompt"]
