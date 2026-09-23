"""상태 스트리밍 저장의 실패 원자성과 기존 텍스트·바이너리 계약."""

import pytest

from services.telegram_bot.core.storage import write_bytes_atomic, write_text_atomic


def test_stream_failure_keeps_previous_state_and_removes_temporary(tmp_path):
    path = tmp_path / "observations.jsonl"
    path.write_text("previous\n", encoding="utf-8")

    def broken_stream():
        yield "first\n"
        raise OSError("source read failed")

    with pytest.raises(OSError, match="source read failed"):
        write_text_atomic(path, broken_stream())
    assert path.read_text(encoding="utf-8") == "previous\n"
    assert list(tmp_path.iterdir()) == [path]


def test_text_and_stream_produce_identical_utf8_bytes(tmp_path):
    text = "사건 하나\n사건 둘\n"
    plain = tmp_path / "plain.txt"
    streamed = tmp_path / "streamed.txt"
    write_text_atomic(plain, text)
    write_text_atomic(streamed, iter(text.splitlines(keepends=True)))
    assert plain.read_bytes() == streamed.read_bytes() == text.encode("utf-8")


def test_binary_state_still_preserves_every_byte(tmp_path):
    path = tmp_path / "chart.png"
    data = bytes(range(256))
    write_bytes_atomic(path, data)
    assert path.read_bytes() == data
