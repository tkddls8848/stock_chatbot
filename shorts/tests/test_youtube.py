import base64
from dataclasses import replace
import hashlib
import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
import requests

from polymarket_shorts import youtube
from polymarket_shorts.config import Settings
from polymarket_shorts.review import ReviewError, complete_review, operation_lock, write_json
from polymarket_shorts.status import current_status

_real_shorts_problem = youtube._shorts_problem


@pytest.fixture(autouse=True)
def _shorts_ok(monkeypatch):
    """가짜 영상 파일은 ffprobe로 읽을 수 없다. 쇼츠 조건 검사는 따로 시험한다."""
    monkeypatch.setattr(youtube, "_shorts_problem", lambda video, settings: None)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    settings = replace(Settings.from_env(), output_dir=tmp_path,
                       google_client_id="client", google_client_secret="secret",
                       youtube_refresh_token="refresh",
                       # 운영 기본값(youtube_privacy="public")에 기대지 않는다.
                       youtube_privacy="private")
    root = tmp_path / "2026-09-27"
    root.mkdir()
    (root / "clip.mp4").write_bytes(b"0123456789")
    write_json(root / "review.json", {
        "status": "reviewed", "video": "clip.mp4", "produced_at": "2026-09-27T21:00:00+09:00",
        "video_sha256": hashlib.sha256(b"0123456789").hexdigest(),
        "youtube": {"title": "오늘의 전망", "description": "집단 예측", "tags": ["경제"]},
    })
    monkeypatch.setattr(youtube.time, "sleep", lambda _: None)
    # 모든 테스트에서 실수로 실제 API를 호출하는 것도 금지한다.
    monkeypatch.setattr(youtube.requests, "request", Mock(side_effect=AssertionError("network forbidden")))
    return root, settings


def response(code=200, payload=None, **headers):
    return Mock(status_code=code, headers=headers, json=Mock(return_value=payload or {}))


def http(monkeypatch, *responses):
    mock = Mock(side_effect=responses)
    monkeypatch.setattr(youtube.requests, "request", mock)
    return mock


def start():
    return response(Location="https://www.googleapis.com/upload/session")


def test_upload_resumes_and_deduplicates(prepared, monkeypatch):
    root, settings = prepared
    mock = http(monkeypatch, response(payload={"access_token": "access"}), start(),
                response(308, Range="bytes=0-3"), response(201, {"id": "video_1"}))
    result = youtube.upload(root, settings)
    assert result == {"status": "uploaded", "video_id": "video_1", "url": "https://www.youtube.com/shorts/video_1"}
    assert mock.call_args_list[-1].kwargs["data"] == b"456789"
    assert mock.call_args_list[-1].kwargs["headers"]["Content-Range"] == "bytes 4-9/10"
    metadata = mock.call_args_list[1].kwargs["json"]
    assert metadata["snippet"] == {"title": "오늘의 전망", "description": "집단 예측", "tags": ["경제"],
                                    "categoryId": "25", "defaultLanguage": "ko"}
    assert metadata["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                                  "containsSyntheticMedia": True}
    assert youtube.upload(root, settings)["status"] == "already_uploaded"
    assert mock.call_count == 4
    status = current_status(settings)
    assert status["uploaded"] is True and status["url"] == result["url"]
    saved = json.loads((root / "upload.json").read_text(encoding="utf-8"))
    entry = next(iter(saved["revisions"].values()))
    assert entry["uploaded_at"] and entry["revision_id"]
    assert "access" not in json.dumps(saved) and "session_url" not in entry


@pytest.mark.parametrize("failure", [requests.ConnectionError("secret"), response(503)])
def test_lost_completion_is_probed_without_reposting(prepared, monkeypatch, failure):
    root, settings = prepared
    mock = http(monkeypatch, response(payload={"access_token": "access"}), start(),
                failure, response(201, {"id": "finished"}))
    assert youtube.upload(root, settings)["video_id"] == "finished"
    assert mock.call_args_list[-1].kwargs["headers"]["Content-Range"] == "bytes */10"
    assert mock.call_args_list[-1].kwargs["data"] == b""


def test_retry_uses_durable_session(prepared, monkeypatch):
    root, settings = prepared
    mock = http(monkeypatch, response(payload={"access_token": "access"}), start(), *[response(503)] * 4)
    with pytest.raises(ReviewError, match="중단"):
        youtube.upload(root, settings)
    mock = http(monkeypatch, response(payload={"access_token": "access"}), response(308),
                response(201, {"id": "retried"}))
    assert youtube.upload(root, settings)["status"] == "uploaded"
    assert mock.call_args_list[1].args[0] == "PUT"


def test_not_reviewed_and_missing_credentials_do_not_call_http(prepared):
    root, settings = prepared
    assert youtube.upload(root, replace(settings, youtube_refresh_token=""))["status"] == "no_credentials"
    record = json.loads((root / "review.json").read_text(encoding="utf-8"))
    record["status"] = "pending"
    write_json(root / "review.json", record)
    assert youtube.upload(root, settings)["status"] == "not_reviewed"
    complete_review(root)
    assert youtube.upload(root, replace(settings, youtube_refresh_token=""))["status"] == "no_credentials"
    assert json.loads((root / "review.json").read_text(encoding="utf-8"))["status"] == "reviewed"


@pytest.mark.parametrize("word", ["Polymarket", "폴리마켓", "베팅", "배팅", "예측 시장", "거래량", "유동성"])
@pytest.mark.parametrize("field", ["title", "description", "tags"])
def test_forbidden_metadata_refused(prepared, word, field):
    root, settings = prepared
    record = json.loads((root / "review.json").read_text(encoding="utf-8"))
    record["youtube"][field] = [word] if field == "tags" else word
    write_json(root / "review.json", record)
    with pytest.raises(ReviewError, match="금지"):
        youtube.upload(root, settings)


def test_changed_video_is_refused(prepared):
    root, settings = prepared
    (root / "clip.mp4").write_bytes(b"changed")
    with pytest.raises(ReviewError, match="변경"):
        youtube.upload(root, settings)


def test_current_revision_must_be_reviewed_even_when_original_uploaded(prepared, monkeypatch):
    root, settings = prepared
    http(monkeypatch, response(payload={"access_token": "access"}), start(), response(201, {"id": "old"}))
    youtube.upload(root, settings)
    revision = root / "revisions" / "new"
    revision.mkdir(parents=True)
    write_json(revision / "review.json", {"status": "pending"})
    write_json(root / "workflow.json", {"current": "revisions/new"})
    assert youtube.upload(root, settings)["status"] == "not_reviewed"
    assert current_status(settings)["uploaded"] is False


def test_token_failures_are_sanitized_and_bounded(prepared, monkeypatch):
    root, settings = prepared
    mock = http(monkeypatch, *[requests.ConnectionError("secret refresh access")] * 4)
    with pytest.raises(ReviewError) as caught:
        youtube.upload(root, settings)
    assert "secret" not in str(caught.value) and caught.value.__suppress_context__
    assert mock.call_count == 4


def test_expired_refresh_token_says_how_to_recover(prepared, monkeypatch):
    """Google은 테스트 상태 OAuth 앱의 refresh token을 7일 뒤 끊는다(2026-10-04 실측: invalid_grant)."""
    root, settings = prepared
    http(monkeypatch, response(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
    with pytest.raises(ReviewError) as caught:
        youtube.upload(root, settings)
    assert "invalid_grant" in str(caught.value) and "SHORTS_YOUTUBE_REFRESH_TOKEN" in str(caught.value)


def test_other_token_rejections_stay_generic(prepared, monkeypatch):
    root, settings = prepared
    http(monkeypatch, response(400, {"error": "invalid_client"}))
    with pytest.raises(ReviewError, match=r"YouTube 요청 거부\(HTTP 400\)"):
        youtube.upload(root, settings)


def test_upload_and_production_share_the_lock(prepared):
    from polymarket_shorts.pipeline import produce_daily
    from datetime import date

    root, settings = prepared
    with operation_lock(root, ".workflow.lock"):
        with pytest.raises(ReviewError, match="처리 중"):
            youtube.upload(root, settings)
        with pytest.raises(ReviewError, match="처리 중"):
            produce_daily(settings, production_date=date(2026, 9, 27), force=True)


def test_session_must_be_persisted_before_sending_video(prepared, monkeypatch):
    root, settings = prepared
    mock = http(monkeypatch, response(payload={"access_token": "access"}), start())
    monkeypatch.setattr(youtube, "write_json", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        youtube.upload(root, settings)
    assert mock.call_count == 2


def test_untrusted_session_url_is_rejected_before_token_is_sent(prepared, monkeypatch):
    root, settings = prepared
    mock = http(monkeypatch, response(payload={"access_token": "access"}),
                response(Location="https://evil.example/upload"))
    with pytest.raises(ReviewError, match="세션 주소"):
        youtube.upload(root, settings)
    assert mock.call_count == 2


def test_server_error_on_session_start_retries_and_308_without_range_restarts(prepared, monkeypatch):
    root, settings = prepared
    mock = http(monkeypatch, response(payload={"access_token": "access"}), response(503), start(),
                response(308), response(201, {"id": "ok"}))
    assert youtube.upload(root, settings)["status"] == "uploaded"
    assert mock.call_args_list[-1].kwargs["data"] == b"0123456789"


def test_oauth_loopback_pkce_and_state(prepared, monkeypatch):
    _, settings = prepared
    captured = {}

    class Server:
        def __init__(self, address, handler):
            assert address == ("127.0.0.1", youtube.AUTH_PORT)
            self.handler = handler

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def handle_request(self):
            callback = object.__new__(self.handler)
            callback.path = "/?state=wrong&code=bad"
            callback.send_response = Mock()
            callback.end_headers = Mock()
            callback.wfile = Mock()
            callback.do_GET()
            callback.send_response.assert_called_with(400)
            callback.path = "/?state=" + captured["state"][0] + "&code=code"
            callback.do_GET()

    def browser(url):
        captured.update(parse_qs(urlsplit(url).query))
        return True

    monkeypatch.setattr(youtube, "HTTPServer", Server)
    monkeypatch.setattr(youtube.webbrowser, "open", browser)
    mock = http(monkeypatch, response(payload={"refresh_token": "refresh-result"}))
    assert youtube.authorize(settings) == "refresh-result"
    assert captured["scope"] == [youtube.SCOPE]
    assert captured["access_type"] == ["offline"] and captured["prompt"] == ["consent"]
    data = mock.call_args.kwargs["data"]
    # 웹 클라이언트에 등록한 고정 주소와 같아야 한다(임의 포트는 redirect_uri_mismatch).
    assert captured["redirect_uri"] == ["http://127.0.0.1:8765/"]
    assert data["redirect_uri"] == "http://127.0.0.1:8765/" and data["code"] == "code"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).decode().rstrip("=")
    assert captured["code_challenge"] == [challenge]


def test_video_that_is_not_a_short_is_not_uploaded(monkeypatch, tmp_path):
    """3분을 넘거나 가로 영상이면 쇼츠로 분류되지 않으므로 올리지 않고 이유를 알린다."""
    import subprocess

    def probe(width, height, duration):
        out = json.dumps({"streams": [{"width": width, "height": height}], "format": {"duration": str(duration)}})
        return Mock(returncode=0, stdout=out)

    settings = Settings.from_env()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: probe(1080, 1920, 190))
    assert "3분" in _real_shorts_problem(tmp_path / "v.mp4", settings)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: probe(1920, 1080, 60))
    assert "가로" in _real_shorts_problem(tmp_path / "v.mp4", settings)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: probe(1080, 1920, 117))
    assert _real_shorts_problem(tmp_path / "v.mp4", settings) is None


def test_links_point_to_the_shorts_address():
    assert youtube.shorts_url("abc") == "https://www.youtube.com/shorts/abc"


def test_new_production_queues_review_without_uploading(prepared):
    from polymarket_shorts import cli
    root, settings = prepared
    record = json.loads((root / "review.json").read_text(encoding="utf-8"))
    write_json(root / "review.json", {**record, "status": "pending"})
    produced = {"status": "pending_review", "date": root.name}
    gate = cli._queue_review(produced, settings)
    assert gate["state"] == "pending" and gate["deadline"] is None
    assert json.loads((root / "review.json").read_text())["status"] == "pending"
    assert cli._queue_review(produced, settings)["token"] == gate["token"]
    assert cli._queue_review({**produced, "status": "already_produced"}, settings) is None
    assert cli._queue_review({**produced, "status": "no_suitable_issues"}, settings) is None


def test_direct_upload_cannot_bypass_scenario_gate(prepared):
    from polymarket_shorts.approval import register
    root, settings = prepared
    register(root, settings)
    # Even legacy complete_review() does not bypass the scenario approval.
    complete_review(root)
    assert youtube.upload(root, settings)["status"] == "not_reviewed"


def test_gate_approval_holds_lock_through_real_upload(prepared, monkeypatch):
    from polymarket_shorts.approval import approve, register
    root, settings = prepared
    gate = register(root, settings)
    http(monkeypatch, response(payload={"access_token": "access"}), start(), response(201, {"id": "approved"}))
    result = approve(settings, gate["token"])
    assert result["status"] == "uploaded"
    assert approve(settings, gate["token"])["status"] == "already_uploaded"


def test_retry_after_new_production_registers_missing_gate_without_enrolling_legacy(prepared):
    from polymarket_shorts import cli
    root, settings = prepared
    result = {"status": "already_produced", "date": root.name}
    assert cli._queue_review(result, settings) is None
    record = json.loads((root / "review.json").read_text())
    write_json(root / "review.json", {**record, "status": "pending", "scenario_review_required": True})
    gate = cli._queue_review(result, settings)
    assert gate["state"] == "pending" and gate["deadline"] is None


def test_upload_publishes_the_latest_video_for_the_web_front_page(prepared, monkeypatch):
    # 웹은 storage/shorts/를 읽지 않는다 — 첫 화면 "오늘의 영상"은 쇼츠가 storage/public/에 쓴 파일만 본다.
    root, settings = prepared
    http(monkeypatch, response(payload={"access_token": "access"}), start(), response(201, {"id": "video_1"}))
    youtube.upload(root, settings)
    saved = json.loads((settings.public_dir / "shorts" / "ko.json").read_text(encoding="utf-8"))
    assert settings.public_dir == settings.output_dir.parent / "public"
    assert {key: saved[key] for key in ("date", "video_id", "url", "title")} == {
        "date": "2026-09-27", "video_id": "video_1",
        "url": "https://www.youtube.com/shorts/video_1", "title": "오늘의 전망"}


def test_latest_published_day_wins_and_unuploaded_days_are_skipped(tmp_path):
    settings = replace(Settings.from_env(), output_dir=tmp_path / "shorts")
    base = settings.output_dir
    for day, video in (("2026-10-07", "older"), ("2026-10-08", "newer")):
        write_json(base / day / "upload.json", {"revisions": {"r": {"video_id": video, "uploaded_at": day}}})
    write_json(base / "2026-10-09" / "upload.json", {"revisions": {"r": {"revision_id": "r"}}})
    assert youtube.publish_latest(base, settings)["video_id"] == "newer"
    saved = json.loads((tmp_path / "public" / "shorts" / "ko.json").read_text(encoding="utf-8"))
    assert saved["date"] == "2026-10-08" and saved["title"] == ""
    assert youtube.publish_latest(tmp_path / "missing", settings) is None
