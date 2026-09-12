from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _media_binary(env_name: str, executable: str) -> str:
    configured = os.getenv(env_name, executable).strip()
    located = shutil.which(configured)
    if located:
        return located
    configured_path = Path(configured)
    if configured_path.is_file():
        return str(configured_path)
    # WinGet 설치 직후에는 현재 프로세스의 PATH가 갱신되지 않는다. Gyan 패키지의
    # 실제 bin 경로를 찾아 새 터미널이나 앱 재시작 없이도 첫 렌더를 진행한다.
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if os.name == "nt" and local_app_data:
        package_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        matches = sorted(
            package_root.glob(f"Gyan.FFmpeg*/ffmpeg-*/bin/{executable}.exe"),
            reverse=True,
        )
        if matches:
            return str(matches[0])
    return configured


@dataclass(frozen=True)
class Settings:
    web_url: str
    timezone: ZoneInfo
    output_dir: Path
    state_file: Path
    max_duration_seconds: float
    target_script_chars: int
    max_groups: int
    tts_voice: str
    tts_rate: str
    font_file: Path | None
    ffmpeg_bin: str
    ffprobe_bin: str
    visuals_enabled: bool
    upload_enabled: bool
    youtube_privacy: str
    youtube_client_secret_file: Path
    youtube_token_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        privacy = os.getenv("SHORTS_YOUTUBE_PRIVACY", "private").strip()
        if privacy not in {"private", "unlisted", "public"}:
            raise ValueError("SHORTS_YOUTUBE_PRIVACY must be private, unlisted, or public")
        maximum = float(os.getenv("SHORTS_MAX_DURATION_SECONDS", "119"))
        if not 30 <= maximum <= 119:
            raise ValueError("SHORTS_MAX_DURATION_SECONDS must be between 30 and 119")
        font = os.getenv("SHORTS_FONT_FILE", "").strip()
        return cls(
            web_url=os.getenv("POLYMARKET_WEB_URL", "https://nunchi.live").rstrip("/"),
            timezone=ZoneInfo(os.getenv("SHORTS_TIMEZONE", "Asia/Seoul")),
            output_dir=PROJECT_DIR / "output",
            state_file=PROJECT_DIR / "state" / "published.json",
            max_duration_seconds=maximum,
            target_script_chars=max(300, int(os.getenv("SHORTS_TARGET_SCRIPT_CHARS", "760"))),
            max_groups=max(1, int(os.getenv("SHORTS_MAX_GROUPS", "3"))),
            tts_voice=os.getenv("SHORTS_TTS_VOICE", "ko-KR-SunHiNeural"),
            tts_rate=os.getenv("SHORTS_TTS_RATE", "-4%"),
            font_file=Path(font) if font else None,
            ffmpeg_bin=_media_binary("FFMPEG_BIN", "ffmpeg"),
            ffprobe_bin=_media_binary("FFPROBE_BIN", "ffprobe"),
            visuals_enabled=_bool("SHORTS_VISUALS_ENABLED", True),
            upload_enabled=_bool("SHORTS_UPLOAD_ENABLED"),
            youtube_privacy=privacy,
            youtube_client_secret_file=PROJECT_DIR / os.getenv(
                "YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json"
            ),
            youtube_token_file=PROJECT_DIR / os.getenv(
                "YOUTUBE_TOKEN_FILE", "youtube_token.json"
            ),
        )
