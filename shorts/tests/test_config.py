from pathlib import Path

from polymarket_shorts import config


def test_windows_winget_ffmpeg_is_found_when_path_is_stale(tmp_path, monkeypatch):
    binary = (
        tmp_path
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Gyan.FFmpeg_test"
        / "ffmpeg-9.0-full_build"
        / "bin"
        / "ffprobe.exe"
    )
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("FFPROBE_BIN", "ffprobe")
    monkeypatch.setattr(config.os, "name", "nt")
    monkeypatch.setattr(config.shutil, "which", lambda _: None)

    resolved = config._media_binary("FFPROBE_BIN", "ffprobe")

    assert Path(resolved) == binary
