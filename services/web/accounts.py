"""Google OIDC and private account contexts; no names, emails or Google tokens at rest.

One web worker owns bounded, expiring login transactions and opaque sessions in memory.
Restarting the worker signs everyone out. Account paths come only from verified Google sub.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from services.web.core.clock import now
from services.web.core.storage import FileLockTimeout, file_lock

COOKIE = "__Host-nunchi_session"
FLOW_COOKIE = "__Host-nunchi_oidc"
SESSION_SECONDS = 12 * 3600
FLOW_SECONDS = 600


@dataclass(frozen=True)
class Account:
    key: str
    root: Path


class Accounts:
    def __init__(self, root: Path, *, client_id: str, client_secret: str,
                 identity_key: str, origin: str, ready: bool = True):
        self.root = root
        self.client_id, self.client_secret = client_id, client_secret
        self.identity_key = identity_key
        self.origin = origin.rstrip("/")
        self.configured = bool(client_id and client_secret and len(identity_key) >= 32 and ready
                               and urlsplit(self.origin).scheme == "https")
        self._sessions: dict[str, tuple[str, float]] = {}
        self._flows: dict[str, dict] = {}
        self._lock = threading.RLock()

    def _prune(self):
        stamp = now().timestamp()
        self._sessions = {k: v for k, v in self._sessions.items() if v[1] > stamp}
        self._flows = {k: v for k, v in self._flows.items() if v["expires"] > stamp}

    def issue(self, subject: str) -> str:
        if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            raise ValueError("Invalid subject")
        key = hmac.new(self.identity_key.encode(), subject.encode(), hashlib.sha256).hexdigest()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            if len(self._sessions) >= 4096:
                raise HTTPException(503, "로그인 이용량이 많습니다. 잠시 뒤 다시 시도하세요.")
            self._sessions[hashlib.sha256(token.encode()).hexdigest()] = (key, now().timestamp() + SESSION_SECONDS)
        return token

    def identify(self, request: Request) -> Account:
        if not self.configured:
            raise HTTPException(503, "Google 로그인을 준비 중입니다.")
        token = request.cookies.get(COOKIE, "")
        with self._lock:
            self._prune()
            row = self._sessions.get(hashlib.sha256(token.encode()).hexdigest())
        if not row:
            raise HTTPException(401, "Google 로그인이 필요합니다.")
        return Account(row[0], self.root / row[0])

    def same_origin(self, request: Request):
        if request.headers.get("origin") != self.origin:
            raise HTTPException(403, "같은 사이트에서 다시 요청하세요.")

    def context(self, request: Request):
        account = self.identify(request)
        if request.method not in {"GET", "HEAD"}:
            self.same_origin(request)
        # Serializes mutations, long-running generation and deletion, across threads/processes.
        # Auth is checked again after taking the lock so a deleted session cannot recreate data.
        try:
            with file_lock(self.root / ".locks" / (account.key + ".lock"),
                           stale_seconds=1800, timeout=0.1):
                self.identify(request)
                yield account
        except FileLockTimeout as exc:
            raise HTTPException(409, "다른 개인 작업이 진행 중입니다. 잠시 뒤 다시 시도하세요.") from exc

    def revoke(self, request: Request):
        with self._lock:
            token = request.cookies.get(COOKIE, "")
            self._sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def exchange(self, code: str, flow: dict) -> str:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token

        result = requests.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.origin + "/auth/google/callback", "grant_type": "authorization_code",
            "code_verifier": flow["verifier"],
        }, timeout=15)
        result.raise_for_status()
        # verify_oauth2_token checks signature, expiration, audience and Google's issuer.
        claims = id_token.verify_oauth2_token(result.json()["id_token"], GoogleRequest(), self.client_id)
        if not hmac.compare_digest(str(claims.get("nonce", "")), flow["nonce"]):
            raise ValueError("Invalid nonce")
        if claims.get("azp", self.client_id) != self.client_id:
            raise ValueError("Invalid authorized party")
        return claims["sub"]

    def router(self) -> APIRouter:
        from fastapi import Depends
        router = APIRouter()

        @router.get("/auth/google")
        def google(request: Request, next: str = "/portfolio"):
            if not self.configured:
                raise HTTPException(503, "Google 로그인을 준비 중입니다.")
            destination = next if next in {"/portfolio", "/research"} else "/portfolio"
            state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
            with self._lock:
                self._prune()
                if len(self._flows) >= 1024:
                    raise HTTPException(429, "잠시 뒤 다시 로그인하세요.")
                self._flows[state] = {"browser": browser, "nonce": nonce, "verifier": verifier,
                                      "next": destination, "expires": now().timestamp() + FLOW_SECONDS}
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
                "client_id": self.client_id, "redirect_uri": self.origin + "/auth/google/callback",
                "response_type": "code", "scope": "openid", "state": state, "nonce": nonce,
                "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
            }), status_code=303)
            response.set_cookie(FLOW_COOKIE, browser, max_age=FLOW_SECONDS, secure=True,
                                httponly=True, samesite="lax", path="/")
            return response

        @router.get("/auth/google/callback")
        def callback(request: Request, state: str = "", code: str = "", error: str = ""):
            with self._lock:
                self._prune()
                flow = self._flows.get(state)
                if not flow or not hmac.compare_digest(request.cookies.get(FLOW_COOKIE, ""), flow["browser"]):
                    raise HTTPException(400, "로그인 요청이 만료되었거나 일치하지 않습니다.")
                self._flows.pop(state)
            if error or not code or not self.configured:
                raise HTTPException(400, "Google 로그인이 완료되지 않았습니다.")
            try:
                token = self.issue(self.exchange(code, flow))
            except Exception as exc:
                # Never log tokens, the authorization code, claims or provider response bodies.
                raise HTTPException(401, "Google 인증을 확인하지 못했습니다. 다시 로그인하세요.") from exc
            self.revoke(request)
            response = RedirectResponse(flow["next"], status_code=303)
            response.delete_cookie(FLOW_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
            response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, secure=True,
                                httponly=True, samesite="lax", path="/")
            return response

        @router.get("/api/account/session")
        @router.get("/api/portfolio/session")
        def session(request: Request):
            try:
                self.identify(request)
                unlocked = True
            except HTTPException:
                unlocked = False
            return {"configured": self.configured, "unlocked": unlocked}

        @router.delete("/api/account/session", status_code=204)
        @router.delete("/api/portfolio/session", status_code=204)
        def logout(request: Request, response: Response):
            self.same_origin(request)
            self.revoke(request)
            response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
            response.headers["Clear-Site-Data"] = '"cache", "storage"'

        @router.get("/api/account/export")
        def export(account: Account = Depends(self.context)):
            data = {}
            if account.root.exists():
                for path in account.root.rglob("*.json"):
                    if path.is_symlink():
                        continue
                    data[str(path.relative_to(account.root))] = json.loads(path.read_text(encoding="utf-8"))
            return Response(json.dumps(data, ensure_ascii=False), media_type="application/json",
                            headers={"Content-Disposition": 'attachment; filename="my-data.json"'})

        @router.delete("/api/account", status_code=204)
        def delete(request: Request, response: Response, account: Account = Depends(self.context)):
            # Only the server-derived account directory. Shared/operator storage is never touched.
            if account.root.parent.resolve() != self.root.resolve() or account.root.is_symlink():
                raise HTTPException(500, "계정 저장소를 확인할 수 없습니다.")
            if account.root.exists():
                shutil.rmtree(account.root)
            with self._lock:
                self._sessions = {k: v for k, v in self._sessions.items() if v[0] != account.key}
            response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
            response.headers["Clear-Site-Data"] = '"cache", "storage"'

        return router
