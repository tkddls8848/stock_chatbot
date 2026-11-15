"""검수 완료본만 게시한다. 재개 세션을 보존해 응답 유실 뒤에도 중복을 막는다."""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
from pathlib import Path
import re
import secrets
import sys
import threading
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
COMMENT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
SCOPE = "https://www.googleapis.com/auth/youtube.upload " + COMMENT_SCOPE
API_URL = "https://www.googleapis.com/youtube/v3"
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
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret, **grant,
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
        raise ReviewError("youtube_privacy는 private, unlisted, public 중 하나여야 합니다")
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
            entry = history.get("revisions", {}).get(identifier, {})
            if entry.get("video_id"):
                return {"status": "already_uploaded", "video_id": entry["video_id"],
                        "url": shorts_url(entry["video_id"]),
                        "comment": _comment(target / "upload.json", history, identifier, metadata, settings)}
            problem = _shorts_problem(video, settings)
            if problem:
                return {**empty, "status": "not_shorts", "reason": problem}
            if not all((settings.google_client_id, settings.google_client_secret, settings.youtube_refresh_token)):
                return {**empty, "status": "no_credentials"}
            video_id = _send(target / "upload.json", history, identifier, metadata, video, settings)
            comment = _comment(target / "upload.json", history, identifier, metadata, settings)
            pruned = prune(settings, keep_id=video_id)
        try:
            publish_latest(root.parent, settings)
        except OSError:
            # 게시는 이미 끝났다. 웹 첫 화면은 다음 게시 때 따라잡는다.
            logger.warning("오늘의 영상 공개 파일을 쓰지 못했습니다", exc_info=True)
        return {"status": "uploaded", "video_id": video_id, "url": shorts_url(video_id), "comment": comment,
                "pruned": pruned}


def _send(path: Path, history: dict, identifier: str, metadata: dict, video: Path, settings: Settings) -> str:
    """재개 세션을 먼저 `path`에 남기고 영상을 보낸다. 응답을 잃어도 같은 세션으로 이어 중복 게시를 막는다."""
    token = _token(settings, grant_type="refresh_token", refresh_token=settings.youtube_refresh_token).get("access_token")
    if not isinstance(token, str) or not token:
        raise ReviewError("YouTube 액세스 토큰 발급 실패")
    entries = history.setdefault("revisions", {})
    entry = entries.get(identifier, {})
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
        write_json(path, history)
    video_id = _transfer(_session_url(entry["session_url"]), token, video, resume=resume)
    entry.update(video_id=video_id, url=shorts_url(video_id), uploaded_at=now().isoformat())
    entry.pop("session_url", None)
    write_json(path, history)
    return video_id


def _comment(path: Path, history: dict, identifier: str, metadata: dict, settings: Settings) -> dict:
    """운영자 채널로 설명을 한 번 게시한다. 결과 불명인 POST는 자동 재전송하지 않는다."""
    entry = history["revisions"][identifier]
    previous = entry.get("comment", {})
    if previous.get("status") in {"posted", "uncertain"}:
        return previous
    result = {"status": "failed"}
    try:
        credentials = _token(settings, grant_type="refresh_token", refresh_token=settings.youtube_refresh_token)
        if COMMENT_SCOPE not in credentials.get("scope", "").split():
            raise ReviewError("댓글 권한이 없습니다. 운영자 계정으로 --youtube-auth를 다시 승인하세요")
        token = credentials.get("access_token")
        if not isinstance(token, str) or not token:
            raise ReviewError("YouTube 액세스 토큰 발급 실패")
        headers = {"Authorization": f"Bearer {token}"}
        channels = _json(_request("GET", API_URL + "/channels", headers=headers,
                                 params={"part": "id", "mine": "true"})).get("items", [])
        videos = _json(_request("GET", API_URL + "/videos", headers=headers,
                               params={"part": "snippet", "id": entry["video_id"]})).get("items", [])
        channel = videos[0].get("snippet", {}).get("channelId") if videos else None
        if not channel or channel not in {item.get("id") for item in channels}:
            raise ReviewError("인증 계정이 영상 운영자 채널과 다릅니다")
        # POST 전에 남긴다. 응답을 잃으면 수동 확인 전 재전송하지 않는다.
        result = {"status": "uncertain", "reason": "댓글 게시 결과 확인 필요: 중복 방지를 위해 재전송하지 않습니다"}
        entry["comment"] = result
        write_json(path, history)
        try:
            response = requests.request("POST", API_URL + "/commentThreads", headers=headers,
                                        params={"part": "snippet"}, json={"snippet": {
                                            "channelId": channel, "videoId": entry["video_id"],
                                            "topLevelComment": {"snippet": {
                                                "textOriginal": metadata["snippet"]["description"]}}}},
                                        timeout=(15, 120), allow_redirects=False)
        except requests.RequestException:
            return result
        if 400 <= response.status_code < 500:
            result = {"status": "failed"}
        elif not 200 <= response.status_code < 300:
            return result
        body = _json(response)
        comment_id = body.get("id")
        if not isinstance(comment_id, str) or not comment_id:
            raise ReviewError("댓글 ID를 확인하지 못했습니다")
        result = {"status": "posted", "id": comment_id, "posted_at": now().isoformat()}
    except ReviewError as exc:
        result = {**result, "reason": str(exc)}
        logger.warning("영상 게시 완료, 댓글 미완료: %s", exc)
    entry["comment"] = result
    write_json(path, history)
    return result


# 버그로 대량 삭제되는 것을 막는 상한
MAX_PRUNE_DELETIONS = 20


def prune(settings: Settings, *, keep_id: str) -> dict:
    """정리 실패는 이미 끝난 업로드를 실패로 바꾸지 않는다."""
    result = {"status": "skipped", "deleted": [], "kept": None, "reason": ""}
    try:
        with operation_lock(settings.output_dir / "state", ".prune.lock"):
            _prune(settings, keep_id, result)
    except Exception:
        # 예외 문자열에도 자격값·응답 본문이 있을 수 있으므로 고정 문구만 반환한다.
        result.update(status="failed", reason="쇼츠 정리 중 오류가 발생했습니다. 다음 게시 때 다시 시도합니다")
    return result


def _prune(settings: Settings, keep_id: str, result: dict) -> None:
    from .pipeline import VIDEO_TITLE_PATTERN

    credentials = _token(settings, grant_type="refresh_token", refresh_token=settings.youtube_refresh_token)
    if COMMENT_SCOPE not in credentials.get("scope", "").split():
        result["reason"] = "오래된 쇼츠를 지울 권한이 없습니다. --youtube-auth를 다시 승인하세요"
        return
    token = credentials.get("access_token")
    if not isinstance(token, str) or not token:
        raise ReviewError("YouTube 액세스 토큰 발급 실패")
    headers = {"Authorization": f"Bearer {token}"}
    channels = _json(_request("GET", API_URL + "/channels", headers=headers,
                             params={"part": "contentDetails", "mine": "true"}))["items"]
    if len(channels) != 1:
        raise ReviewError("운영자 채널을 확인하지 못했습니다")
    channel = channels[0]
    params = {"part": "snippet,contentDetails", "maxResults": 50,
              "playlistId": channel["contentDetails"]["relatedPlaylists"]["uploads"]}
    videos = {}
    while True:
        page = _json(_request("GET", API_URL + "/playlistItems", headers=headers, params=dict(params)))
        for item in page["items"]:
            snippet, details = item["snippet"], item["contentDetails"]
            if VIDEO_TITLE_PATTERN.fullmatch(snippet.get("title", "")):
                videos[details["videoId"]] = {
                    "video_id": details["videoId"], "title": snippet["title"],
                    "published_at": details.get("videoPublishedAt") or snippet["publishedAt"],
                }
        if not page.get("nextPageToken"):
            break
        params["pageToken"] = page["nextPageToken"]
    result["kept"] = len(videos)
    if keep_id not in videos:
        result["reason"] = "방금 올린 영상이 아직 채널 목록에 없어 다음 게시 때 정리합니다"
        return
    if settings.youtube_keep_shorts < 1:
        raise ReviewError("쇼츠 보존 개수는 1 이상이어야 합니다")
    excess = len(videos) - settings.youtube_keep_shorts
    if excess <= 0:
        result["reason"] = "보존 개수 이하이므로 정리할 쇼츠가 없습니다"
        return
    candidates = sorted((row for key, row in videos.items() if key != keep_id),
                        key=lambda row: datetime.fromisoformat(row["published_at"].replace("Z", "+00:00")))
    path = settings.output_dir / "state" / "pruned.json"
    history = _read(path)
    deleted = history.setdefault("deleted", [])
    failures, skipped = [], []
    for row in candidates[:min(excess, MAX_PRUNE_DELETIONS)]:
        video_id = row["video_id"]
        try:
            items = _json(_request("GET", API_URL + "/videos", headers=headers,
                                   params={"part": "snippet", "id": video_id}))["items"]
            if (len(items) != 1 or items[0].get("snippet", {}).get("channelId") != channel["id"]
                    or not VIDEO_TITLE_PATTERN.fullmatch(items[0]["snippet"].get("title", ""))):
                skipped.append(video_id)
                continue
            response = _request("DELETE", API_URL + "/videos", headers=headers, params={"id": video_id})
            if response.status_code not in (204, 404):
                failures.append(video_id)
                continue
        except Exception:
            failures.append(video_id)
            continue
        result["deleted"].append(video_id)
        result["kept"] -= 1
        deleted.append({**row, "deleted_at": now().isoformat()})
        # 저장 실패는 밖에서 실패로 돌리고 더 삭제하지 않는다.
        write_json(path, history)
    result["status"] = "failed" if failures else ("pruned" if result["deleted"] else "skipped")
    reasons = []
    if failures:
        reasons.append(f"영상 {len(failures)}개 삭제 실패")
    if skipped:
        reasons.append(f"영상 {len(skipped)}개 소유 채널·제목 재확인 불일치")
    if result["kept"] > settings.youtube_keep_shorts:
        reasons.append("남은 초과분은 다음 실행에서 정리합니다")
    result["reason"] = "; ".join(reasons)


def publish_latest(base: Path, settings: Settings) -> dict | None:
    """공개 웹 첫 화면의 "오늘의 영상"이 읽는 최신 게시본을 `storage/public/shorts/ko.json`에 쓴다.

    웹은 `storage/shorts/`를 읽지 않으므로(공개 라우트는 `storage/public/`만) 쇼츠가 공개할 것만 따로 쓴다.
    `base`는 제작일 폴더들이 있는 `storage/shorts/`이고, 가장 최근 날짜의 마지막 게시본을 고른다.
    """
    payload = latest_published(base)
    if payload:
        write_json(settings.public_dir / "shorts" / "ko.json", payload)
    return payload


def latest_published(base: Path) -> dict | None:
    """웹 공개와 수동 정리가 함께 쓰는 최신 제작일의 마지막 게시본."""
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
        return payload
    return None


# 승인 결과를 받는 운영자 PC의 루프백 주소. 웹 애플리케이션 클라이언트는 등록한 주소만 받으므로(임의 포트는
# 데스크톱 앱 유형만 허용) 포트를 고정하고 이 주소를 웹 로그인 클라이언트의 리디렉션 URI에 함께 등록한다.
AUTH_PORT = 8765
AUTH_REDIRECT = f"http://127.0.0.1:{AUTH_PORT}/"


AUTH_WAIT_SECONDS = 300
# 터미널이 붙여 넣은 글자 앞뒤에 다는 표시(bracketed paste). 입력으로 읽히면 주소가 깨진다.
_PASTE_MARKS = re.compile(r"\x1b\[20[01]~")


def authorize(settings: Settings) -> str:
    """고정 루프백 + state + PKCE. 루프백 또는 붙여 넣은 주소로 승인을 받는다. 토큰은 호출자가 화면에만 출력한다."""
    if not settings.google_client_id or not settings.google_client_secret:
        raise ReviewError(".env에 GOOGLE_CLIENT_ID와 GOOGLE_CLIENT_SECRET을 설정하세요")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    result = {}

    def _accept(address: str) -> bool:
        """루프백으로 받은 경로든 붙여 넣은 전체 주소든, state가 맞을 때만 승인 코드를 받는다."""
        parts = urlsplit(address)
        query = parse_qs(parts.query)
        valid = parts.path == "/" and secrets.compare_digest(query.get("state", [""])[0], state)
        if valid:
            result.update(code=query.get("code", [""])[0], error=bool(query.get("error")))
        return valid

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            valid = _accept(self.path)
            self.send_response(200 if valid else 400)
            self.end_headers()
            self.wfile.write(b"Return to your terminal." if valid else b"Invalid callback.")

    try:
        server = HTTPServer(("127.0.0.1", AUTH_PORT), Callback)
    except OSError as exc:
        raise ReviewError(f"127.0.0.1:{AUTH_PORT} 포트를 쓰는 프로그램을 닫고 다시 실행하세요") from exc
    with server:
        server.timeout = 1
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": settings.google_client_id, "redirect_uri": AUTH_REDIRECT,
            "response_type": "code", "scope": SCOPE, "access_type": "offline", "prompt": "consent",
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })
        # 주소는 늘 출력하고 두 길을 함께 기다린다. 같은 기기의 브라우저면 루프백으로 바로 돌아오고, 다른 기기의
        # 브라우저(서버 SSH 등)면 127.0.0.1:8765가 "연결할 수 없음"으로 뜨므로 그 주소창 전체를 붙여 넣는다.
        # 브라우저가 있는지 스스로 판단하지 않는다 — 운영 서버는 가상 화면(DISPLAY)과 BROWSER가 있어
        # 열기가 "성공"으로 끝나지만 사람이 보는 화면에는 아무것도 뜨지 않았다(2026-10-10).
        print("아래 주소를 브라우저에서 열어 채널 소유자 계정으로 승인하세요:\n" + url, file=sys.stderr)
        print(f"다른 기기에서 승인했다면 열리지 않는 {AUTH_REDIRECT} 페이지의 주소창 전체를 여기 붙여 넣으세요:",
              file=sys.stderr)
        if sys.platform in ("win32", "darwin"):
            webbrowser.open(url)

        def _read_pasted():
            for line in sys.stdin:
                if _accept(_PASTE_MARKS.sub("", line).strip()):
                    return
                print("이번 승인 요청의 주소가 아닙니다. 새로 출력된 주소로 승인한 뒤 다시 붙여 넣으세요:", file=sys.stderr)

        threading.Thread(target=_read_pasted, daemon=True).start()
        deadline = time.monotonic() + AUTH_WAIT_SECONDS
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result.get("code") or result.get("error"):
        raise ReviewError("YouTube 승인이 거부되었거나 5분 안에 완료되지 않았습니다")
    token = _token(settings, grant_type="authorization_code", code=result["code"],
                   redirect_uri=AUTH_REDIRECT, code_verifier=verifier).get("refresh_token")
    if not isinstance(token, str) or not token:
        raise ReviewError("리프레시 토큰이 없습니다. 앱 승인을 해제하고 다시 승인하세요")
    return token
