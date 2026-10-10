from pathlib import Path

from polymarket_shorts import config

from conftest import windows_only


@windows_only
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
    monkeypatch.setattr(config.os, "name", "nt")
    monkeypatch.setattr(config.shutil, "which", lambda _: None)

    resolved = config._media_binary("ffprobe")

    assert Path(resolved) == binary


def test_review_timeout_is_one_hour_and_bounded():
    import pytest
    from dataclasses import replace
    settings = config.Settings.from_env()
    assert settings.review_timeout_minutes == 60
    for value in (0, -1, 1441, 1.5):
        with pytest.raises(ValueError):
            replace(settings, review_timeout_minutes=value)


def test_env_holds_only_secrets_and_tuning_stays_in_code(monkeypatch):
    # 튜닝값을 .env에 적어도 읽지 않는다 — 바꾸려면 코드를 고쳐 git에 남긴다.
    monkeypatch.setenv("SHORTS_EDITOR_MODEL", "@cf/other/model")
    monkeypatch.setenv("SHORTS_AUTO_PUBLISH", "false")
    monkeypatch.setenv("SHORTS_YOUTUBE_REFRESH_TOKEN", "refresh-secret")
    settings = config.Settings.from_env()
    assert settings.editor_model == "@cf/deepseek-ai/deepseek-v4-flash-0731"
    assert settings.auto_publish is True and settings.youtube_privacy == "public"
    assert settings.youtube_refresh_token == "refresh-secret" and "refresh-secret" not in repr(settings)
