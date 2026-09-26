"""선택된 flux PNG만 fal 영상으로 확장한다. 원고·이미지 생성과 독립된 선택 경로다."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import logging
import math
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import requests

from .config import Settings
from .media import _BACKGROUND_PROMPT
from .scenario import Scene


logger = logging.getLogger(__name__)
QUEUE_URL = "https://queue.fal.run"
TOTAL_SECONDS = 600
POLL_SECONDS = 3
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
_MOTION = (
    " Very slow camera drift at a constant speed. Preserve the original composition; "
    "no new objects, text, letters or people. No cuts or sudden brightness changes."
)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("clip deadline exceeded")
    return remaining


def validate_clip(path: Path, settings: Settings, deadline: float) -> None:
    """메타데이터만 정상인 깨진 파일도 거르도록 전체 영상 스트림을 디코드한다."""
    result = subprocess.run([
        settings.ffprobe_bin, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name:format=duration", "-of", "json", str(path),
    ], capture_output=True, text=True, check=True, timeout=min(20, _remaining(deadline)))
    info = json.loads(result.stdout)
    video = info["streams"][0]
    duration = float(info["format"]["duration"])
    if (not math.isfinite(duration) or duration < 4 or not video["codec_name"]
            or int(video["width"]) < 720 or int(video["height"]) < 1280):
        raise ValueError("clip must be at least 4 seconds and 720x1280")
    subprocess.run([
        settings.ffmpeg_bin, "-v", "error", "-xerror", "-threads", "1", "-i", str(path),
        "-map", "0:v:0", "-an", "-f", "null", "-",
    ], capture_output=True, check=True, timeout=min(45, _remaining(deadline)))


def _queue_json(session, method: str, url: str, settings: Settings, deadline: float, **kwargs):
    # 키는 fal queue에만 보낸다. API가 돌려준 URL을 그대로 쓰되 외부 리다이렉트는 따르지 않는다.
    if not isinstance(url, str):
        raise ValueError("invalid fal queue URL")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "queue.fal.run":
        raise ValueError("invalid fal queue URL")
    with session.request(method, url, headers={"Authorization": f"Key {settings.video_api_key}"},
                         timeout=min(30, _remaining(deadline)), allow_redirects=False, **kwargs) as response:
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("unexpected queue redirect")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid fal response")
        return payload


def _generate_clip(png: Path, subject: str, settings: Settings, deadline: float) -> Path | None:
    target = png.with_suffix(".mp4")
    temporary = None
    try:
        _remaining(deadline)
        if target.is_file():
            try:
                validate_clip(target, settings, deadline)
                _remaining(deadline)
                return target
            except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
                logger.warning("클립 캐시 검증 실패, 정지 배경 사용: %s", target.name)
                return None
        # REST 계약 근거 (2026-09-26):
        # https://fal.ai/docs/documentation/model-apis/inference/queue
        # POST /{model} -> status_url/response_url; GET status -> COMPLETED;
        # GET response_url -> 모델 결과 객체. 경로를 조립하지 않고 응답 URL을 따른다.
        # https://fal.ai/models/bytedance/seedance-2.0/fast/image-to-video/api
        # image_url은 data URI 허용, duration은 문자열 enum, 결과는 video.url이다.
        with requests.Session() as session:
            job = _queue_json(session, "POST", f"{QUEUE_URL}/{settings.video_model}", settings, deadline,
                              json={
                                  "image_url": "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode(),
                                  "prompt": _BACKGROUND_PROMPT.format(subject=subject) + _MOTION,
                                  "duration": str(settings.clip_seconds), "resolution": "720p",
                                  "aspect_ratio": "9:16", "generate_audio": False,
                              })
            while True:
                status = _queue_json(session, "GET", job["status_url"], settings, deadline)
                if status.get("error"):
                    raise ValueError("fal generation failed")
                if status["status"] == "COMPLETED":
                    break
                if status["status"] not in {"IN_QUEUE", "IN_PROGRESS"}:
                    raise ValueError("unexpected fal status")
                time.sleep(min(POLL_SECONDS, _remaining(deadline)))
            result = _queue_json(session, "GET", job["response_url"], settings, deadline)
            url = result["video"]["url"]
            if not isinstance(url, str) or urlsplit(url).scheme != "https":
                raise ValueError("invalid clip download URL")
            # 스트리밍으로 내려받아 동시 다운로드가 메모리 예산을 잠식하지 않게 한다.
            with session.get(url, stream=True, timeout=min(30, _remaining(deadline))) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(dir=png.parent, suffix=".mp4", delete=False) as stream:
                    temporary = Path(stream.name)
                    size = 0
                    for block in response.iter_content(chunk_size=64 * 1024):
                        _remaining(deadline)
                        size += len(block)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise ValueError("clip exceeds download limit")
                        stream.write(block)
            validate_clip(temporary, settings, deadline)
            _remaining(deadline)
            temporary.replace(target)
        return target
    except (requests.RequestException, OSError, ValueError, KeyError, IndexError, TypeError,
            subprocess.SubprocessError) as exc:
        # HTTP 예외에는 서명 URL·응답 원문이 들어갈 수 있어 종류와 파일 이름만 기록한다.
        logger.warning("클립 생성 실패, 정지 배경 사용: %s (%s)", png.name, type(exc).__name__)
        return None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("클립 임시 파일 정리 실패: %s", temporary.name)


def clips_for(scenes: tuple[Scene, ...], backgrounds: tuple[Path | None, ...],
              settings: Settings) -> tuple[Path | None, ...]:
    """최대 이슈 수만 동시 제출한다. 전체 상한 뒤에는 작업 완료를 기다리지 않는다."""
    if not settings.generated_clips or not settings.video_api_key or not settings.visuals_enabled:
        return backgrounds
    first = next((scene.visual_query for scene in scenes if scene.kind == "consensus"), "")
    jobs = {}
    for scene, path in zip(scenes, backgrounds, strict=True):
        query = first if scene.kind == "intro" else scene.visual_query
        if (path is None or scene.kind == "outro" or scene.background == "still"
                or "topic:" not in query):
            continue
        # 저장 기본 그림은 영상 API에 보내지 않는다. 이미 만든 이슈 해시 PNG만 대상이다.
        if path.name == hashlib.sha1(query.encode()).hexdigest()[:12] + ".png":
            jobs[path] = query.split("topic:", 1)[1].strip()
    if not jobs:
        return backgrounds
    deadline = time.monotonic() + TOTAL_SECONDS
    pool = ThreadPoolExecutor(max_workers=min(5, settings.max_groups))
    futures = {pool.submit(_generate_clip, png, subject, settings, deadline): png
               for png, subject in list(jobs.items())[:settings.max_groups]}
    made = {}
    try:
        for future in as_completed(futures, timeout=_remaining(deadline)):
            made[futures[future]] = future.result()
    except TimeoutError:
        logger.warning("클립 전체 대기 상한 초과, 미완료 이슈는 정지 배경 사용")
    finally:
        # context manager의 shutdown(wait=True)는 10분 상한을 무력화한다.
        # 실행 중 요청도 같은 deadline을 확인해 캐시를 뒤늦게 교체하지 않는다.
        pool.shutdown(wait=False, cancel_futures=True)
    return tuple((made.get(path) or path) if scene.background != "still" and scene.kind != "outro" else path
                 for scene, path in zip(scenes, backgrounds, strict=True))


def visual_payload(backgrounds: tuple[Path | None, ...]) -> list[dict | None]:
    return [({"asset": path.name, "source": "Seedance image-to-video", "kind": "clip", "generated": True}
             if path.suffix == ".mp4" else
             {"asset": path.name, "source": "GPT Image / built-in", "generated": True})
            if path else None for path in backgrounds]


def review_details(backgrounds: tuple[Path | None, ...]) -> dict:
    # 도입에서 공유한 클립은 두 번 세지 않는다. 정지로 바꾼 이슈의 클립이 도입에만
    # 남아 있어도 합성 배경 표시는 필요하다.
    count = len({path for path in backgrounds if path and path.suffix == ".mp4"})
    return {"clip_issues": count} if count else {}
