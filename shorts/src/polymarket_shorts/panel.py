"""브라우저에서 로컬 쇼츠를 검수하고 자연어로 수정한다."""
from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import webbrowser

from .config import Settings
from .review import REVIEW_FILE, ReviewError, complete_review, operation_lock, read_script
from .workflow import _read, current_target, revise


PANEL_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NUNCHI 쇼츠 검수</title>
<style>
:root { color-scheme: dark; --bg:#0b0f0e; --panel:#141a18; --line:#29332f;
  --gold:#e7bb62; --muted:#9aa7a1; --text:#f1f4f2; --danger:#e78578; }
* { box-sizing:border-box; }
body { margin:0; background:radial-gradient(circle at 85% 5%,#20332b 0,transparent 32%),var(--bg);
  color:var(--text); font:15px/1.55 system-ui,"Noto Sans KR",sans-serif; }
header { height:64px; display:flex; align-items:center; justify-content:space-between;
  padding:0 28px; border-bottom:1px solid var(--line); position:sticky; top:0;
  background:#0b0f0eee; backdrop-filter:blur(12px); z-index:2; }
.brand { color:var(--gold); font-weight:800; letter-spacing:.12em; }
.status { padding:5px 10px; border:1px solid var(--line); border-radius:999px; color:var(--muted); }
main { max-width:1380px; margin:0 auto; padding:28px; display:grid;
  grid-template-columns:minmax(310px,430px) minmax(0,1fr); gap:28px; }
.left { position:sticky; top:92px; align-self:start; }
video { width:100%; max-height:72vh; background:#000; border-radius:18px; box-shadow:0 20px 70px #0009; }
.card { background:#141a18e8; border:1px solid var(--line); border-radius:16px; padding:20px; margin-bottom:18px; }
h1 { font-size:24px; margin:0 0 8px; } h2 { font-size:17px; margin:0 0 14px; }
.muted { color:var(--muted); } .meta { display:flex; gap:14px; flex-wrap:wrap; margin:12px 0 0; }
textarea { width:100%; min-height:112px; resize:vertical; border:1px solid #425149; border-radius:12px;
  padding:13px; background:#0c110f; color:var(--text); font:inherit; }
textarea:focus { outline:2px solid #e7bb6266; border-color:var(--gold); }
.actions { display:flex; gap:10px; margin-top:12px; }
button { border:0; border-radius:10px; padding:11px 16px; font-weight:750; cursor:pointer; }
button.primary { background:var(--gold); color:#17130b; flex:1; }
button.secondary { background:#26312c; color:var(--text); }
button:disabled { opacity:.45; cursor:wait; }
.message { min-height:24px; margin-top:10px; color:var(--gold); }
.message.error { color:var(--danger); }
.scene { border-top:1px solid var(--line); padding:16px 0; }
.scene:first-child { border-top:0; padding-top:0; }
.scene-head { display:flex; justify-content:space-between; gap:16px; }
.scene h3 { margin:0 0 8px; font-size:16px; } .metric { color:var(--gold); font-weight:750; }
.label { color:var(--muted); font-size:12px; letter-spacing:.08em; margin:10px 0 3px; }
.copy { white-space:pre-wrap; margin:0; } details pre { white-space:pre-wrap; overflow:auto; color:#cbd4d0; }
@media (max-width:900px) { main { grid-template-columns:1fr; padding:16px; } .left { position:static; }
  video { max-height:68vh; } header { padding:0 16px; } }
</style>
</head>
<body>
<header><div class="brand">NUNCHI / REVIEW</div><div id="status" class="status">불러오는 중</div></header>
<main>
  <section class="left">
    <video id="video" controls playsinline></video>
    <div class="card">
      <h2>자연어로 수정</h2>
      <textarea id="instruction" maxlength="4000" placeholder="예: 첫 멘트를 질문형으로 바꾸고 두 번째 장면을 쉽게 줄여줘"></textarea>
      <div class="actions"><button id="revise" class="primary">수정하고 다시 렌더</button><button id="complete" class="secondary">검수 완료</button></div>
      <div id="message" class="message"></div>
    </div>
  </section>
  <section>
    <div class="card"><h1 id="title"></h1><div id="description" class="copy muted"></div><div id="meta" class="meta"></div></div>
    <div class="card"><h2>장면별 원고</h2><div id="scenes"></div></div>
    <details class="card"><summary>review.md 원문</summary><pre id="script"></pre></details>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
let state;
function node(tag, text, cls) { const el=document.createElement(tag); if(text!==undefined) el.textContent=text; if(cls) el.className=cls; return el; }
function render(data) {
  state=data; $('status').textContent=data.status==='reviewed'?'검수 완료':'검수 대기';
  $('title').textContent=data.youtube.title; $('description').textContent=data.youtube.description;
  $('meta').replaceChildren(node('span',`${data.duration_seconds.toFixed(1)}초`,'muted'), node('span',`${data.scenes.length}개 장면`,'muted'), node('span',`음성 ${data.tts_rate}`,'muted'));
  $('video').src='/video?v='+encodeURIComponent(data.video_sha256); $('script').textContent=data.script;
  const scenes=data.scenes.map((scene,index)=>{ const box=node('article',undefined,'scene'); const head=node('div',undefined,'scene-head');
    head.append(node('h3',`${String(index+1).padStart(2,'0')} · ${scene.title.replaceAll('\\n',' / ')}`),node('div',[scene.metric_label,scene.metric].filter(Boolean).join(' · '),'metric')); box.append(head);
    box.append(node('div','화면 문구','label'),node('p',scene.takeaway||scene.body||'-','copy'),node('div','멘트','label'),node('p',scene.narration,'copy')); return box; });
  $('scenes').replaceChildren(...scenes); $('complete').disabled=data.status==='reviewed';
}
async function api(path, body) { const response=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
  const data=await response.json(); if(!response.ok) throw new Error(data.error||'요청에 실패했습니다'); return data; }
async function refresh(){ render(await api('/api/state')); }
function busy(value,message=''){ $('revise').disabled=value; $('complete').disabled=value||(state&&state.status==='reviewed'); $('message').className='message'; $('message').textContent=message; }
$('revise').onclick=async()=>{ const instruction=$('instruction').value.trim(); if(!instruction)return; busy(true,'수정안을 만들고 음성·영상을 렌더하고 있습니다…');
  try { const result=await api('/api/revise',{instruction}); render(result.state); $('instruction').value=''; busy(false,result.summary); } catch(error){ busy(false); $('message').className='message error'; $('message').textContent=error.message; } };
$('complete').onclick=async()=>{ busy(true,'완료 상태를 기록하고 있습니다…'); try { const result=await api('/api/complete',{}); render(result.state); busy(false,'검수를 완료했습니다.'); } catch(error){ busy(false); $('message').className='message error'; $('message').textContent=error.message; } };
refresh().catch(error=>{ $('message').className='message error'; $('message').textContent=error.message; });
</script>
</body></html>"""


def panel_state(root: Path) -> dict:
    target = current_target(root)
    record = _read(target / REVIEW_FILE)
    scenario = _read(target / "scenario.json")
    production_path = target / "production.json"
    production = _read(production_path) if production_path.exists() else {}
    video = target / record["video"]
    if not video.is_file():
        raise ReviewError(f"영상이 없습니다: {video}")
    return {
        "status": record["status"],
        "date": record["date"],
        "duration_seconds": float(record["duration_seconds"]),
        "video_sha256": record["video_sha256"],
        "youtube": record["youtube"],
        "scenes": scenario["scenes"],
        "script": read_script(target),
        "tts_rate": production.get("rate", "+0%"),
    }


def _range(value: str | None, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or not any(match.groups()):
        raise ValueError("잘못된 Range 요청")
    first, last = match.groups()
    if not first:
        count = int(last)
        if count <= 0:
            raise ValueError("잘못된 Range 요청")
        return max(0, size - count), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("범위를 벗어난 Range 요청")
    return start, min(end, size - 1)


def _handler(root: Path, settings: Settings):
    class PanelHandler(BaseHTTPRequestHandler):
        server_version = "PolymarketShortsPanel/1"

        def log_message(self, format: str, *args) -> None:
            return

        def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, exc: Exception) -> None:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ReviewError("요청 길이가 올바르지 않습니다") from exc
            if not 0 < length <= 8192:
                raise ReviewError("요청 크기는 1~8192바이트여야 합니다")
            try:
                value = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ReviewError("JSON 요청이 필요합니다") from exc
            if not isinstance(value, dict):
                raise ReviewError("JSON 객체가 필요합니다")
            return value

        def _video(self) -> None:
            target = current_target(root)
            record = _read(target / REVIEW_FILE)
            video = (target / record["video"]).resolve()
            if not video.is_relative_to(target.resolve()) or not video.is_file():
                raise ReviewError("영상 경로가 올바르지 않습니다")
            size = video.stat().st_size
            try:
                requested = _range(self.headers.get("Range"), size)
            except ValueError:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            start, end = requested or (0, size - 1)
            self.send_response(HTTPStatus.PARTIAL_CONTENT if requested else HTTPStatus.OK)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if requested:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with video.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = stream.read(min(1 << 20, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

        def do_GET(self) -> None:
            try:
                if self.path == "/" or self.path.startswith("/?"):
                    body = PANEL_HTML.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/api/state":
                    self._json(panel_state(root))
                elif self.path == "/video" or self.path.startswith("/video?"):
                    self._video()
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            except ConnectionError:
                # 브라우저는 탐색 위치를 바꾸거나 새 Range 요청을 열 때 기존 영상
                # 연결을 취소한다. 이미 끊긴 소켓에는 오류 응답을 다시 쓰지 않는다.
                return
            except (ReviewError, OSError, ValueError, KeyError) as exc:
                self._error(exc)

        def do_POST(self) -> None:
            try:
                body = self._body()
                if self.path == "/api/revise":
                    if set(body) != {"instruction"} or not isinstance(body["instruction"], str):
                        raise ReviewError("instruction 문자열만 보낼 수 있습니다")
                    _, summary = revise(root, body["instruction"], settings)
                    self._json({"summary": summary, "state": panel_state(root)})
                elif self.path == "/api/complete":
                    if body:
                        raise ReviewError("완료 요청에는 값이 필요하지 않습니다")
                    with operation_lock(root, ".workflow.lock"):
                        complete_review(current_target(root))
                    self._json({"state": panel_state(root)})
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            except ConnectionError:
                # 렌더 중 탭을 닫아 응답 연결이 사라진 경우에도 결과는 보존한다.
                return
            except (ReviewError, OSError, ValueError, KeyError) as exc:
                self._error(exc)

    return PanelHandler


def serve(root: Path, settings: Settings, *, port: int = 8765, open_browser: bool = True) -> None:
    root = root.resolve()
    panel_state(root)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), _handler(root, settings))
    except OSError as exc:
        raise ReviewError(f"로컬 검수 패널을 열지 못했습니다: {exc}") from exc
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"쇼츠 검수 패널: {url}")
    print("종료하려면 이 창에서 Ctrl+C를 누르세요.")
    if open_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n검수 패널을 종료했습니다.")
    finally:
        server.server_close()
