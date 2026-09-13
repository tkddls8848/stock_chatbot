"""관리 웹의 FastAPI 앱 구성과 생명주기 관리.

`start_web_admin(app)`은 uvicorn 서버를 현재 이벤트 루프의 백그라운드
태스크로 띄우고, `stop_web_admin(app)`은 이를 정리한다. 두 함수는 봇
진입점(`bot/main.py`)이 web_admin 기능 활성 시 post_init / post_shutdown
훅에서 호출한다.

데이터 접근 방침: 다른 기능이 소유한 데이터는 bot_data에 설치된 매니저를
통해 읽는다. 파일 직접 읽기(_read_json/_tail_jsonl)는 해당 기능이 꺼져
매니저가 없을 때의 읽기 전용 폴백이다. 봇과 이벤트 루프를 공유하므로
이 동기 읽기는 반드시 asyncio.to_thread로 넘긴다(루프를 막으면 텔레그램
폴링까지 함께 멈춘다).
"""

import asyncio
import json
import logging
import secrets
from pathlib import Path
from typing import Any

from telegram_bot.core.config import (
    NEWS_LOG_FILE,
    PREDICTION_LOG_FILE,
    RESEARCH_STATE_FILE,
    WATCHLIST_EVENTS_FILE,
    WEB_ADMIN_HOST,
    WEB_ADMIN_PASSWORD,
    WEB_ADMIN_PORT,
    WEB_ADMIN_USER,
)

logger = logging.getLogger(__name__)

_WEB_SERVER_KEY = "web_admin_server"
_WEB_TASK_KEY = "web_admin_task"


# ── 데이터 접근 헬퍼 ──────────────────────────────────

def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _tail_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


# ── FastAPI 앱 구성 ───────────────────────────────────

def build_app(bot_app):
    """텔레그램 Application을 캡처한 FastAPI 앱을 만든다."""
    from fastapi import Depends, FastAPI, HTTPException, status
    from fastapi.responses import HTMLResponse
    from fastapi.security import HTTPBasic, HTTPBasicCredentials

    api = FastAPI(title="Stock Chatbot 관리", docs_url=None, redoc_url=None)
    security = HTTPBasic()

    def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
        user_ok = secrets.compare_digest(credentials.username, WEB_ADMIN_USER)
        pass_ok = secrets.compare_digest(credentials.password, WEB_ADMIN_PASSWORD)
        if not (user_ok and pass_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="인증 실패",
                headers={"WWW-Authenticate": "Basic"},
            )
        return credentials.username

    def _bot_data() -> dict:
        return bot_app.bot_data

    # ── 대시보드 HTML ─────────────────────────────────
    @api.get("/", response_class=HTMLResponse)
    async def index(_: str = Depends(require_auth)) -> str:
        return _DASHBOARD_HTML

    # ── 시스템 상태 ───────────────────────────────────
    @api.get("/api/status")
    async def status_view(_: str = Depends(require_auth)):
        data = _bot_data()
        registry = data.get("feature_registry")
        scheduler = data.get("scheduler")

        jobs = []
        if scheduler is not None:
            for job in scheduler.get_jobs():
                nxt = getattr(job, "next_run_time", None)
                jobs.append({"id": job.id, "next_run": nxt.isoformat() if nxt else None})

        return {
            "features": sorted(registry.enabled_keys) if registry else [],
            "scheduler_running": bool(scheduler and scheduler.running),
            "jobs": jobs,
        }

    # ── 관심종목 ─────────────────────────────────────
    @api.get("/api/watchlist")
    async def watchlist_view(_: str = Depends(require_auth)):
        manager = _bot_data().get("watchlist_manager")
        if manager is None:
            return {}
        return await manager.get_all()

    @api.post("/api/watchlist")
    async def watchlist_add(payload: dict, _: str = Depends(require_auth)):
        manager = _bot_data().get("watchlist_manager")
        if manager is None:
            raise HTTPException(status_code=404, detail="관심종목 기능이 비활성입니다.")
        code = str(payload.get("code", "")).strip()
        if not code:
            raise HTTPException(status_code=400, detail="종목 코드가 필요합니다.")
        stock_db = _bot_data().get("stock_db")
        if stock_db is None:
            raise HTTPException(
                status_code=503,
                detail="종목 DB 기능을 사용할 수 없습니다.",
            )
        resolved = stock_db.resolve_code(code)
        if resolved is None:
            raise HTTPException(
                status_code=400,
                detail="종목 DB에 없는 코드입니다.",
            )
        name = str(payload.get("name", "")).strip()
        if not name:
            name = stock_db.get_display_name(resolved) or resolved
        await manager.add(resolved, name)
        return {"code": resolved, "name": name}

    @api.delete("/api/watchlist/{code}")
    async def watchlist_remove(code: str, _: str = Depends(require_auth)):
        manager = _bot_data().get("watchlist_manager")
        if manager is None:
            raise HTTPException(status_code=404, detail="관심종목 기능이 비활성입니다.")
        removed = await manager.remove(code.strip())
        if removed is None:
            raise HTTPException(status_code=404, detail="목록에 없는 종목입니다.")
        return {"code": code, "name": removed}

    # ── 뉴스 로그 ─────────────────────────────────────
    @api.get("/api/news")
    async def news_view(hours: int = 24, _: str = Depends(require_auth)):
        news_log = _bot_data().get("news_log")
        if news_log is not None:
            return await news_log.snapshot(since_hours=hours)
        return await asyncio.to_thread(_read_json, NEWS_LOG_FILE, [])

    # ── 관심종목 이벤트 ───────────────────────────────
    @api.get("/api/events")
    async def events_view(days: int = 30, _: str = Depends(require_auth)):
        events = _bot_data().get("watchlist_events")
        if events is not None:
            return await events.snapshot(lookback_days=days)
        return await asyncio.to_thread(_read_json, WATCHLIST_EVENTS_FILE, [])

    # ── 리서치 후보 ───────────────────────────────────
    @api.get("/api/research")
    async def research_view(_: str = Depends(require_auth)):
        manager = _bot_data().get("market_view_manager")
        if manager is not None:
            return {
                "sight": manager.get_sight(),
                "history": manager.get_history_summaries(),
            }
        return await asyncio.to_thread(_read_json, RESEARCH_STATE_FILE, {})

    # ── 종목별 뉴스 감성 로그 ─────────────────────────
    @api.get("/api/predictions")
    async def predictions_view(days: int = 7, _: str = Depends(require_auth)):
        prediction_log = _bot_data().get("prediction_log")
        if prediction_log is not None:
            return await prediction_log.snapshot(max(1, min(days, 90)))
        return await asyncio.to_thread(_tail_jsonl, PREDICTION_LOG_FILE, 500)

    return api


# ── 생명주기 ─────────────────────────────────────────

async def _serve_guarded(server, host: str, port: int) -> None:
    """uvicorn 실패를 봇 이벤트 루프에서 격리한다.

    uvicorn은 바인드 실패 시 `sys.exit()`을 호출한다(uvicorn/server.py).
    태스크 안에서 난 SystemExit은 asyncio가 이벤트 루프 밖으로 다시 던져
    `run_polling()`까지 통째로 무너뜨리므로 반드시 여기서 받아야 한다.
    CancelledError는 BaseException이라 여기 걸리지 않고
    `stop_web_admin`의 종료 경로로 그대로 전달된다.
    """
    try:
        await server.serve()
    except SystemExit:
        logger.warning(
            "[WebAdmin] %s:%d 바인드에 실패해 관리 웹 없이 계속합니다. "
            "다른 프로세스가 쓰고 있거나 Windows 예약 포트 구간일 수 있습니다"
            "(`netsh int ipv4 show excludedportrange protocol=tcp`). "
            "WEB_ADMIN_PORT를 비어 있는 포트로 바꾸세요.",
            host,
            port,
        )
    except Exception as e:  # noqa: BLE001 - 관리 웹 실패가 봇을 멈추면 안 된다.
        logger.warning("[WebAdmin] 관리 웹이 중단됐습니다: %s", e, exc_info=True)


async def start_web_admin(bot_app) -> None:
    """관리 웹을 백그라운드 태스크로 시작한다.

    비활성이거나 비밀번호 미설정이면 조용히 건너뛴다. 의존성 미설치나
    구동 실패는 봇 본체에 영향을 주지 않도록 경고만 남기고 넘어간다.
    """
    if not WEB_ADMIN_PASSWORD:
        logger.info("[WebAdmin] WEB_ADMIN_PASSWORD 미설정으로 관리 웹을 시작하지 않습니다.")
        return

    try:
        import uvicorn
    except ImportError:
        logger.warning(
            "[WebAdmin] fastapi/uvicorn 미설치로 관리 웹을 시작하지 못했습니다. "
            "`pip install -r requirements.txt` 후 다시 시도하세요."
        )
        return

    try:
        api = build_app(bot_app)
    except ImportError:
        logger.warning("[WebAdmin] fastapi 미설치로 관리 웹을 구성하지 못했습니다.")
        return

    config = uvicorn.Config(
        api,
        host=WEB_ADMIN_HOST,
        port=WEB_ADMIN_PORT,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # 봇이 신호(SIGINT/SIGTERM)를 관리하므로 uvicorn의 신호 처리는 끈다.
    server.install_signal_handlers = lambda: None

    task = asyncio.create_task(_serve_guarded(server, WEB_ADMIN_HOST, WEB_ADMIN_PORT))
    bot_app.bot_data[_WEB_SERVER_KEY] = server
    bot_app.bot_data[_WEB_TASK_KEY] = task
    logger.info(
        "[WebAdmin] 관리 웹 시작: http://%s:%d (사용자=%s)",
        WEB_ADMIN_HOST,
        WEB_ADMIN_PORT,
        WEB_ADMIN_USER,
    )


async def stop_web_admin(bot_app) -> None:
    """구동 중인 관리 웹을 정리한다."""
    server = bot_app.bot_data.pop(_WEB_SERVER_KEY, None)
    task = bot_app.bot_data.pop(_WEB_TASK_KEY, None)
    if server is None:
        return
    server.should_exit = True
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        except Exception as e:  # noqa: BLE001 - 종료 경로는 실패해도 넘긴다.
            logger.warning("[WebAdmin] 관리 웹 종료 중 오류: %s", e)
    logger.info("[WebAdmin] 관리 웹을 종료했습니다.")


_DASHBOARD_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stock Chatbot 관리</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0;
         background: #f5f6f8; color: #1c1e21; }
  @media (prefers-color-scheme: dark) {
    body { background: #16181c; color: #e6e6e6; }
    .card { background: #22252b !important; border-color: #333 !important; }
    th { background: #2a2e35 !important; }
    input { background: #1c1e22; color: #e6e6e6; border-color: #444; }
  }
  header { padding: 16px 20px; background: #b71c1c; color: #fff; }
  header h1 { margin: 0; font-size: 18px; }
  main { max-width: 960px; margin: 0 auto; padding: 16px; }
  .card { background: #fff; border: 1px solid #e2e4e8; border-radius: 10px;
          padding: 16px; margin-bottom: 16px; }
  .card h2 { margin: 0 0 12px; font-size: 15px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #e2e4e8; }
  th { background: #f0f1f3; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
  input { padding: 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 13px; }
  button { padding: 8px 12px; border: 0; border-radius: 6px; background: #b71c1c;
           color: #fff; cursor: pointer; font-size: 13px; }
  button.ghost { background: #6b7280; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px;
           font-size: 12px; background: #e5e7eb; color: #111; }
  .badge.on { background: #16a34a; color: #fff; }
  .muted { color: #6b7280; font-size: 12px; }
  pre { overflow-x: auto; font-size: 12px; background: rgba(127,127,127,.1);
        padding: 10px; border-radius: 6px; }
</style>
</head>
<body>
<header><h1>📈 Stock Chatbot 관리 대시보드</h1></header>
<main>
  <div class="card">
    <h2>시스템 상태</h2>
    <div id="status">불러오는 중…</div>
  </div>

  <div class="card">
    <h2>관심종목</h2>
    <div class="row">
      <input id="code" placeholder="종목 코드 (예: 600519)">
      <input id="name" placeholder="종목명 (선택)">
      <button onclick="addWatch()">추가</button>
      <button class="ghost" onclick="loadWatch()">새로고침</button>
    </div>
    <div id="watchlist">불러오는 중…</div>
  </div>

  <div class="card">
    <h2>최근 뉴스 (24시간)</h2>
    <button class="ghost" onclick="loadNews()">새로고침</button>
    <div id="news" style="margin-top:12px">불러오는 중…</div>
  </div>

  <div class="card">
    <h2>리서치 후보</h2>
    <button class="ghost" onclick="loadResearch()">새로고침</button>
    <pre id="research" style="margin-top:12px">불러오는 중…</pre>
  </div>
</main>
<script>
async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(await res.text().catch(() => res.status));
  return res.status === 204 ? null : res.json();
}
function esc(s) {
  return String(s ?? "").replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}

async function loadStatus() {
  const el = document.getElementById('status');
  try {
    const s = await api('/api/status');
    const sched = s.scheduler_running
      ? `<span class="badge on">스케줄러 동작</span>` : `<span class="badge">중지</span>`;
    const jobs = s.jobs.map(j =>
      `<tr><td>${esc(j.id)}</td><td class="muted">${esc(j.next_run || '-')}</td></tr>`).join('');
    el.innerHTML = `<div class="row">${sched}</div>
      <div class="muted">활성 기능: ${s.features.map(esc).join(', ')}</div>
      <table style="margin-top:12px"><tr><th>작업</th><th>다음 실행</th></tr>${jobs}</table>`;
  } catch (e) { el.textContent = '오류: ' + e.message; }
}

async function loadWatch() {
  const el = document.getElementById('watchlist');
  try {
    const w = await api('/api/watchlist');
    const rows = Object.entries(w).map(([code, name]) =>
      `<tr><td>${esc(code)}</td><td>${esc(name)}</td>
       <td><button class="ghost" onclick="delWatch('${esc(code)}')">삭제</button></td></tr>`).join('');
    el.innerHTML = rows
      ? `<table><tr><th>코드</th><th>종목명</th><th></th></tr>${rows}</table>`
      : '<div class="muted">등록된 관심종목이 없습니다.</div>';
  } catch (e) { el.textContent = '오류: ' + e.message; }
}
async function addWatch() {
  const code = document.getElementById('code').value.trim();
  const name = document.getElementById('name').value.trim();
  if (!code) return;
  try {
    await api('/api/watchlist', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code, name}) });
    document.getElementById('code').value = '';
    document.getElementById('name').value = '';
    loadWatch();
  } catch (e) { alert('추가 실패: ' + e.message); }
}
async function delWatch(code) {
  if (!confirm(code + ' 을(를) 삭제할까요?')) return;
  try { await api('/api/watchlist/' + encodeURIComponent(code), { method: 'DELETE' }); loadWatch(); }
  catch (e) { alert('삭제 실패: ' + e.message); }
}

async function loadNews() {
  const el = document.getElementById('news');
  try {
    const items = await api('/api/news?hours=24');
    if (!items.length) { el.innerHTML = '<div class="muted">최근 뉴스가 없습니다.</div>'; return; }
    const rows = items.slice(-50).reverse().map(n =>
      `<tr><td class="muted">${esc(n.ts || '')}</td>
       <td>${esc(n.title || n.source || '')}</td></tr>`).join('');
    el.innerHTML = `<table><tr><th>시각</th><th>제목</th></tr>${rows}</table>`;
  } catch (e) { el.textContent = '오류: ' + e.message; }
}

async function loadResearch() {
  const el = document.getElementById('research');
  try { el.textContent = JSON.stringify(await api('/api/research'), null, 2); }
  catch (e) { el.textContent = '오류: ' + e.message; }
}

loadStatus(); loadWatch(); loadNews(); loadResearch();
setInterval(loadStatus, 30000);
</script>
</body>
</html>"""
