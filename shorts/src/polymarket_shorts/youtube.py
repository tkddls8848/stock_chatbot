"""검수 완료본만 게시한다. 재개 세션을 보존해 응답 유실 뒤에도 중복을 막는다."""
from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

import requests

from .config import Settings
from .core.clock import now
from .core.storage import write_json
from .review import ReviewError, _digest, operation_lock
from .workflow import current_target

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
logger = logging.getLogger(__name__)


def _request(method: str, url: str, **kwargs):
    # 응답 본문·요청 URL·requests 예외에는 자격값이 포함될 수 있다.
    for attempt in range(4):
        try:
            response = requests.request(method, url, timeout=(15, 120), allow_redirects=False, **kwargs)
        except requests.RequestException:
            response = None
        if response is not None and response.status_code < 500:
            return response
        if attempt < 3:
            time.sleep(2 ** attempt)
    raise ReviewError("YouTube 연결 실패: 잠시 후 /shorts upload로 재시도하세요") from None


def _json(response) -> dict:
    if not 200 <= response.status_code < 300:
        raise ReviewError(f"YouTube 요청 거부(HTTP {response.status_code})")
    try:
        value = response.json()
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    raise ReviewError("YouTube 응답 형식 오류")


def _token(settings: Settings, **grant) -> dict:
    response = _request("POST", TOKEN_URL, data={
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret, **grant,
    })
    # 갱신 토큰이 끊기면 HTTP 400만으로는 무엇을 할지 모른다 — 2026-10-04부터 나흘 동안 "요청 거부(HTTP 400)"만
    # 남기고 업로드가 멈췄다. 오류 본문의 `error` 코드만 읽는다(자격값은 성공 응답에만 있다).
    if grant.get("grant_type") == "refresh_token" and response.status_code == 400 and _oauth_error(response) == "invalid_grant":
        raise ReviewError(
            "YouTube 승인이 만료되었거나 취소되었습니다(invalid_grant). 운영자 PC에서 --youtube-auth로 새 refresh token을"
            " 받아 서버 .env의 SHORTS_YOUTUBE_REFRESH_TOKEN을 바꾸세요. OAuth 앱이 테스트 상태면 7일마다 만료됩니다")
    return _json(response)


def _oauth_error(response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return str(body.get("error") or "") if isinstance(body, dict) else ""


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (OSError, ValueError):
        pass
    raise ReviewError("업로드/검수 기록을 읽을 수 없습니다. 기록을 확인하세요")


def revision_id(target: Path, record: dict) -> str:
    payload = [target.name, record.get("produced_at"), record.get("video_sha256"), record.get("youtube")]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def uploaded_record(target: Path, record: dict) -> dict:
    return _read(target / "upload.json").get("revisions", {}).get(revision_id(target, record), {})


def _metadata(record: dict, settings: Settings) -> dict:
    metadata = record.get("youtube") or {}
    if (not isinstance(metadata.get("title"), str) or not metadata["title"].strip()
            or not isinstance(metadata.get("description"), str)
            or not isinstance(metadata.get("tags"), list)
            or not all(isinstance(tag, str) for tag in metadata["tags"])):
        raise ReviewError("게시 제목·설명·태그를 확인하세요")
    text = " ".join([metadata["title"], metadata["description"], *metadata["tags"]])
    compact = re.sub(r"\s+", "", text).casefold()
    if any(word in compact for word in ("polymarket", "폴리마켓", "베팅", "배팅", "예측시장", "거래량", "유동성",
                                             "betting", "predictionmarket", "wager", "gambl")):
        raise ReviewError("게시 문구에 금지된 출처 이름 또는 표현이 있어 업로드를 거부합니다")
    if settings.youtube_privacy not in {"private", "unlisted", "public"}:
        raise ReviewError("SHORTS_YOUTUBE_PRIVACY는 private, unlisted, public 중 하나여야 합니다")
    return {"snippet": {**{key: metadata[key] for key in ("title", "description", "tags")},
                        "categoryId": settings.youtube_category_id,
                        "defaultLanguage": "ko"},
            "status": {"privacyStatus": settings.youtube_privacy,
                       "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}


def _session_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname == "www.googleapis.com"
                 and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except ValueError:
        valid = False
    if not valid:
        raise ReviewError("YouTube 업로드 세션 주소가 올바르지 않습니다")
    return value


def _transfer(url: str, token: str, video: Path, *, resume: bool) -> str:
    size, offset, failures = video.stat().st_size, 0, 0
    probe = resume
    with video.open("rb") as stream:
        while failures < 4:
            stream.seek(offset)
            data = b"" if probe else stream.read(8 * 1024 * 1024)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                       "Content-Length": str(len(data)),
                       "Content-Range": (f"bytes */{size}" if probe else
                                         f"bytes {offset}-{offset + len(data) - 1}/{size}")}
            try:
                response = requests.request("PUT", url, data=data, headers=headers,
                                            timeout=(15, 120), allow_redirects=False)
            except requests.RequestException:
                response = None
            if response is None or response.status_code >= 500:
                time.sleep(2 ** failures)
                failures += 1
                probe = True
                continue
            if response.status_code in (200, 201):
                identifier = _json(response).get("id")
                if not isinstance(identifier, str) or not re.fullmatch(r"[\w-]+", identifier):
                    raise ReviewError("YouTube 영상 ID가 없습니다. 같은 명령으로 세션을 확인하세요")
                return identifier
            if response.status_code != 308:
                _json(response)
                raise ReviewError("YouTube 업로드 응답이 올바르지 않습니다")
            match = re.fullmatch(r"bytes=0-(\d+)", response.headers.get("Range", ""))
            if response.headers.get("Range") and not match:
                raise ReviewError("YouTube 재개 위치가 올바르지 않습니다")
            next_offset = int(match[1]) + 1 if match else 0
            if not offset <= next_offset <= size:
                raise ReviewError("YouTube 재개 위치가 영상 범위를 벗어났습니다")
            if next_offset == offset:
                failures += 1
            else:
                failures = 0
            offset, probe = next_offset, next_offset == size
    raise ReviewError("YouTube 업로드가 중단됐습니다. /shorts upload로 이어서 올리세요")


# YouTube API에는 "쇼츠로 올리기" 옵션이 없다. 세로(또는 정사각)이고 3분 이하인 영상을
# YouTube가 쇼츠로 분류한다. 그래서 올리기 전에 이 조건을 확인하고, 링크도 쇼츠 주소로 준다.
SHORTS_MAX_SECONDS = 180


def shorts_url(video_id: str) -> str:
    return f"https://www.youtube.com/shorts/{video_id}"


def _shorts_problem(video: Path, settings: Settings) -> str | None:
    """쇼츠 조건(세로·180초 이하)을 어기면 그 이유."""
    import json as _json_module
    import subprocess

    result = subprocess.run(
        [settings.ffprobe_bin, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", str(video)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        return "영상 정보를 읽지 못했습니다"
    info = _json_module.loads(result.stdout)
    width, height = info["streams"][0]["width"], info["streams"][0]["height"]
    duration = float(info["format"]["duration"])
    if width > height:
        return f"가로 영상({width}x{height})은 쇼츠로 분류되지 않습니다"
    if duration > SHORTS_MAX_SECONDS:
        return f"{duration:.0f}초 영상은 3분을 넘어 쇼츠로 분류되지 않습니다"
    return None


def upload(root: Path, settings: Settings, *, workflow_locked: bool = False) -> dict:
    from contextlib import nullcontext

    empty = {"video_id": None, "url": None}
    requested = root.resolve()
    # 수정본 경로로 호출해도 날짜 폴더의 승인 게이트를 우회할 수 없다.
    for parent in (requested, *requested.parents):
        if not parent.is_relative_to(settings.output_dir.resolve()):
            break
        if (parent / "approval.json").is_file():
            root = parent
            break
    with nullcontext() if workflow_locked else operation_lock(root, ".workflow.lock"):
        target = current_target(root)
        if (root / "approval.json").is_file():
            from .approval import _checked, _read as read_gate
            gate = read_gate(root / "approval.json")
            gate, _ = _checked(root, gate.get("token", ""))
            if requested not in {root.resolve(), target.resolve()}:
                raise ReviewError("이전 수정본은 업로드할 수 없습니다")
            if gate["state"] not in {"approved", "uploaded"}:
                return {**empty, "status": "not_reviewed"}
        with operation_lock(target):
            record = _read(target / "review.json")
            if record.get("status") != "reviewed":
                return {**empty, "status": "not_reviewed"}
            metadata = _metadata(record, settings)
            video = (target / str(record.get("video", ""))).resolve()
            if not video.is_relative_to(target.resolve()) or not video.is_file() or not video.stat().st_size:
                raise ReviewError("검수한 영상 파일이 없습니다")
            if _digest(video) != record.get("video_sha256"):
                raise ReviewError("검수 후 영상이 변경됐습니다. 다시 제작·검수하세요")
            identifier = revision_id(target, record)
            history = _read(target / "upload.json")
            entries = history.setdefault("revisions", {})
            entry = entries.get(identifier, {})
            if entry.get("video_id"):
                return {"status": "already_uploaded", "video_id": entry["video_id"],
                        "url": shorts_url(entry["video_id"])}
            problem = _shorts_problem(video, settings)
            if problem:
                return {**empty, "status": "not_shorts", "reason": problem}
            if not all((settings.youtube_client_id, settings.youtube_client_secret, settings.youtube_refresh_token)):
                return {**empty, "status": "no_credentials"}
            token = _token(settings, grant_type="refresh_token", refresh_token=settings.youtube_refresh_token).get("access_token")
            if not isinstance(token, str) or not token:
                raise ReviewError("YouTube 액세스 토큰 발급 실패")
            resume = bool(entry.get("session_url"))
            if not resume:
                response = _request("POST", UPLOAD_URL, json=metadata, headers={
                    "Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(video.stat().st_size),
                })
                if response.status_code not in (200, 201):
                    _json(response)
                entry = {"revision_id": identifier, "session_url": _session_url(response.headers.get("Location", ""))}
                entries[identifier] = entry
                write_json(target / "upload.json", history)
            video_id = _transfer(_session_url(entry["session_url"]), token, video, resume=resume)
            entry.update(video_id=video_id, url=shorts_url(video_id),
                         uploaded_at=now().isoformat())
            entry.pop("session_url", None)
            write_json(target / "upload.json", history)
        try:
            publish_latest(root.parent, settings)
        except OSError:
            # 게시는 이미 끝났다. 웹 첫 화면은 다음 게시 때 따라잡는다.
            logger.warning("오늘의 영상 공개 파일을 쓰지 못했습니다", exc_info=True)
        return {"status": "uploaded", "video_id": video_id, "url": shorts_url(video_id)}


def publish_latest(base: Path, settings: Settings) -> dict | None:
    """공개 웹 첫 화면의 "오늘의 영상"이 읽는 최신 게시본을 `storage/public/shorts/ko.json`에 쓴다.

    웹은 `storage/shorts/`를 읽지 않으므로(공개 라우트는 `storage/public/`만) 쇼츠가 공개할 것만 따로 쓴다.
    `base`는 제작일 폴더들이 있는 `storage/shorts/`이고, 가장 최근 날짜의 마지막 게시본을 고른다.
    """
    if not base.is_dir():
        return None
    for day in sorted((p for p in base.iterdir() if p.is_dir() and _DAY.fullmatch(p.name)), reverse=True):
        try:
            revisions = _read(day / "upload.json").get("revisions", {})
        except ReviewError:
            continue
        uploaded = [entry for entry in revisions.values()
                    if isinstance(entry, dict) and re.fullmatch(r"[\w-]+", str(entry.get("video_id") or ""))]
        if not uploaded:
            continue
        entry = max(uploaded, key=lambda row: str(row.get("uploaded_at") or ""))
        try:
            title = (_read(current_target(day) / "review.json").get("youtube") or {}).get("title")
        except ReviewError:
            title = None
        payload = {"date": day.name, "video_id": entry["video_id"],
                   "url": shorts_url(entry["video_id"]), "title": title if isinstance(title, str) else "",
                   "uploaded_at": entry.get("uploaded_at") or ""}
        write_json(settings.public_dir / "shorts" / "ko.json", payload)
        return payload
    return None


def authorize(settings: Settings) -> str:
    """데스크톱 루프백 + state + PKCE. 토큰은 호출자가 화면에만 출력한다."""
    if not settings.youtube_client_id or not settings.youtube_client_secret:
        raise ReviewError(".env에 SHORTS_YOUTUBE_CLIENT_ID와 SHORTS_YOUTUBE_CLIENT_SECRET을 설정하세요")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            valid = urlsplit(self.path).path == "/" and secrets.compare_digest(query.get("state", [""])[0], state)
            if valid:
                result.update(code=query.get("code", [""])[0], error=bool(query.get("error")))
            self.send_response(200 if valid else 400)
            self.end_headers()
            self.wfile.write(b"Return to your terminal." if valid else b"Invalid callback.")

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 1
        redirect = f"http://127.0.0.1:{server.server_port}/"
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": settings.youtube_client_id, "redirect_uri": redirect,
            "response_type": "code", "scope": SCOPE, "access_type": "offline", "prompt": "consent",
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })
        if not webbrowser.open(url):
            raise ReviewError("승인 브라우저를 열 수 없습니다. 운영자 PC에서 실행하세요")
        deadline = time.monotonic() + 300
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result.get("code") or result.get("error"):
        raise ReviewError("YouTube 승인이 거부되었거나 5분 안에 완료되지 않았습니다")
    token = _token(settings, grant_type="authorization_code", code=result["code"],
                   redirect_uri=redirect, code_verifier=verifier).get("refresh_token")
    if not isinstance(token, str) or not token:
        raise ReviewError("리프레시 토큰이 없습니다. 앱 승인을 해제하고 다시 승인하세요")
    return token
