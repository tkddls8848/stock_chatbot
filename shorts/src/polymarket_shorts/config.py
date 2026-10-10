from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_DIR.parent / ".env")

# 설정 저장 방침은 봇·웹과 같다(`services/telegram_bot/core/config.py` 머리말). 루트 `.env`에는
# 비밀값(Cloudflare·Google 자격증명, YouTube 갱신 토큰)과 저장 경로(`STORAGE_DIR`)만 두고, 운영자가 조정하는
# 값(모델·음성·공개 범위·자동 승인 등)은 아래 `Settings`의 기본값으로 둔다 — 바꾸면 git에 남는다.


def _media_binary(executable: str) -> str:
    located = shutil.which(executable)
    if located:
        return located
    if executable == "blender":
        if os.name == "nt":
            root = Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Blender Foundation"
            matches = sorted(root.glob("Blender */blender.exe"), reverse=True)
            if matches:
                return str(matches[0])
        return executable
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
    return executable


def _storage_dir() -> Path:
    """공유 저장소(`storage/`). 봇·웹과 같은 `STORAGE_DIR`을 읽는다(`code_guide.md`의
    「공유 저장소」). 비면 저장소 루트의 `storage/`다. 쇼츠 산출물은 `storage/shorts/`에
    두고, 봇의 텔레그램 `/shorts`가 이 CLI의 `--status`로 그 상태를 묻는다.
    """
    configured = os.getenv("STORAGE_DIR", "").strip()
    return Path(configured) if configured else PROJECT_DIR.parent / "storage"


@dataclass(frozen=True)
class Settings:
    output_dir: Path
    state_file: Path
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    blender_bin: str = "blender"
    web_url: str = "https://nunchi.live"
    timezone: ZoneInfo = ZoneInfo("Asia/Seoul")
    max_duration_seconds: float = 150.0
    target_script_chars: int = 1000
    # 이슈 장면 수 상한. 화면·대사 구성이 5개까지만 맞춰져 있다.
    max_groups: int = 5
    tts_voice: str = "ko-KR-SunHiNeural"
    tts_rate: str = "+0%"
    visuals_enabled: bool = True
    editor_account_id: str = ""
    editor_api_token: str = field(default="", repr=False)
    # 이슈 선별·원고·자연어 편집·롱폼 원고 모델(Cloudflare Workers AI).
    editor_model: str = "@cf/deepseek-ai/deepseek-v4-flash-0731"
    # 추론 모델(deepseek-v4 등)의 reasoning_effort. "none"이면 생각 단계를 끈다 — 켜 두면
    # 생각이 토큰 상한과 시간을 먹어 원고가 잘린다(실측 2026-09-28). 비우면 보내지 않는다.
    editor_reasoning_effort: str = "none"
    # 그날 이슈에 맞춘 배경 생성. 끄거나 실패하면 저장된 기본 배경을 쓴다.
    generated_backgrounds: bool = True
    image_model: str = "@cf/black-forest-labs/flux-1-schnell"
    # 그린 배경에 사람·글자가 있는지 보는 비전 모델. Llama 3.2 Vision은 계정의 라이선스
    # 동의가 필요하고 Gemma는 이 계정에 열려 있지 않아 LLaVA를 쓴다(2026-09-27 실측).
    background_check: bool = True
    vision_model: str = "@cf/llava-hf/llava-1.5-7b-hf"
    # 최근 며칠 안에 다룬 이벤트·주제는 후보에서 뺀다(매일 같은 이슈 반복 방지).
    repeat_days: int = 7
    # 웹 로그인과 같은 Google OAuth 클라이언트(웹 애플리케이션)다. YouTube 업로드 권한은 클라이언트가 아니라
    # 승인 때 요청하는 범위로 받는다 — 프로젝트에 YouTube Data API v3만 켜져 있으면 된다.
    google_client_id: str = field(default="", repr=False)
    google_client_secret: str = field(default="", repr=False)
    youtube_refresh_token: str = field(default="", repr=False)
    youtube_privacy: str = "public"
    youtube_category_id: str = "25"
    # 텔레그램에 원고를 전달한 뒤 검토 시간 동안 응답이 없으면 승인·업로드한다.
    auto_publish: bool = True
    review_timeout_minutes: int = 60
    # 쇼츠와 같은 시각에 만들어 쇼츠가 게시될 때 함께 올리는 시장상황 보고서 롱폼의 시장(운영자 결정 2026-10-10).
    longform_market: str = "US"

    @property
    def public_dir(self) -> Path:
        """공개 웹이 내보내는 `storage/public/`. 쇼츠 산출물(`storage/shorts/`)과 같은 공유 저장소에 있다."""
        return self.output_dir.parent / "public"

    def __post_init__(self) -> None:
        if type(self.review_timeout_minutes) is not int or not 1 <= self.review_timeout_minutes <= 1440:
            raise ValueError("review_timeout_minutes must be an integer from 1 to 1440")

    @classmethod
    def from_env(cls) -> "Settings":
        """`.env`에서는 비밀값과 저장 경로만 읽는다. 나머지는 위 기본값이다."""
        output_dir = _storage_dir() / "shorts"
        return cls(
            output_dir=output_dir,
            state_file=output_dir / "state" / "published.json",
            ffmpeg_bin=_media_binary("ffmpeg"),
            ffprobe_bin=_media_binary("ffprobe"),
            blender_bin=_media_binary("blender"),
            editor_account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip(),
            editor_api_token=os.getenv("CLOUDFLARE_WORKER_AI_API_TOKEN", "").strip(),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
            youtube_refresh_token=os.getenv("SHORTS_YOUTUBE_REFRESH_TOKEN", "").strip(),
        )
