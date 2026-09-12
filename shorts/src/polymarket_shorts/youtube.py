from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class UploadError(RuntimeError):
    pass


def _credentials(token_file: Path, client_secret_file: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credentials = None
    if token_file.is_file():
        credentials = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_file.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials or not credentials.valid:
        raise UploadError(
            "YouTube 인증이 없습니다. 로컬에서 polymarket-shorts-auth를 먼저 실행하세요."
        )
    return credentials


def upload_video(
    video_path: Path,
    metadata: dict[str, Any],
    *,
    token_file: Path,
    client_secret_file: Path,
    privacy: str,
) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    credentials = _credentials(token_file, client_secret_file)
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata["tags"],
            "categoryId": "25",
            "defaultLanguage": "ko",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True),
        notifySubscribers=False,
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = str(response.get("id") or "")
    if not video_id:
        raise UploadError("YouTube가 업로드 ID를 반환하지 않았습니다")
    return video_id


def auth_main() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from .config import Settings

    parser = argparse.ArgumentParser(description="YouTube 업로드 OAuth 최초 인증")
    parser.add_argument("--console", action="store_true", help="브라우저를 자동으로 열지 않음")
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.youtube_client_secret_file.is_file():
        raise SystemExit(f"client secret 파일이 없습니다: {settings.youtube_client_secret_file}")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.youtube_client_secret_file), SCOPES
    )
    credentials = flow.run_local_server(port=0, open_browser=not args.console)
    settings.youtube_token_file.write_text(credentials.to_json(), encoding="utf-8")
    print(f"인증 저장 완료: {settings.youtube_token_file}")

