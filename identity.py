"""Browser-mediated desktop sign-in and New API account provisioning."""
import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
import httpx
from pathlib import Path
from http.cookies import SimpleCookie

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field


def install(p):
    app = p.app
    registration_lock = asyncio.Lock()
    attempts = {}

    def rate(request, scope, limit):
        key = (scope, request.client.host if request.client else "unknown")
        now = time.time()
        attempts[key] = [t for t in attempts.get(key, []) if t > now - 600]
        if len(attempts[key]) >= limit:
            raise HTTPException(429, "操作频繁，请十分钟后重试。")
        attempts[key].append(now)

    class Registration(BaseModel):
        username: str = Field(min_length=3, max_length=20, pattern=r"^[a-zA-Z0-9_]+$")
        password: str = Field(min_length=10, max_length=20)

    class Start(BaseModel):
        challenge: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")

    class Poll(BaseModel):
        id: str = Field(min_length=16, max_length=128)
        secret: str = Field(min_length=20, max_length=128)
        verifier: str = Field(min_length=43, max_length=128)

    class Approval(BaseModel):
        id: str = Field(min_length=16, max_length=128)

    @app.post("/api/register")
    async def register(body: Registration, request: Request):
        if not p.SETTINGS.get("registration_enabled"):
            raise HTTPException(403, "注册尚未开放。")
        rate(request, "register", 3)
        socket = p.SETTINGS.get("provisioning_socket", "/run/starbridge-provisioner/provisioner.sock")
        async with registration_lock:
            try:
                transport = httpx.AsyncHTTPTransport(uds=socket)
                async with httpx.AsyncClient(transport=transport, base_url="http://provisioner", timeout=30) as helper:
                    r = await helper.post("/create", json={"username": body.username, "password": body.password})
                if r.status_code != 200:
                    raise HTTPException(400 if r.status_code == 400 else 503, "账号已存在，请直接登录；或注册服务暂不可用。")
            except httpx.RequestError:
                raise HTTPException(503, "注册服务暂不可用，请稍后重试。") from None
        # Account creation is authoritative in New API. If Key setup fails, login can retry.
        response = await p.login(p.Login(username=body.username, password=body.password), request)
        cookie = SimpleCookie(response.headers["set-cookie"])
        session = p.sessions[cookie[p.COOKIE].value]
        models = await p.available_models(session)
        if models:
            await p.build_configuration(session, [models[0]["id"]])
        return response

    @app.post("/api/desktop/start")
    async def start(body: Start, request: Request):
        rate(request, "start", 20)
        ident, secret = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
        code = secrets.token_hex(3).upper()
        with p.database() as db:
            if db.execute("SELECT COUNT(*) FROM devices WHERE expires>?", (time.time(),)).fetchone()[0] > 1000:
                raise HTTPException(503, "登录请求较多，请稍后重试。")
            db.execute("INSERT INTO devices VALUES (?,?,?,?,?,?,?)", (ident, p.digest(secret), body.challenge, code, time.time()+300, None, 0))
        return {"id": ident, "secret": secret, "code": code, "expires_in": 300,
                "verification_url": p.SETTINGS["public_url"].rstrip("/") + "/?desktop=" + ident}

    @app.get("/api/desktop/request")
    async def device_request(id: str, request: Request):
        p.auth(request)
        with p.database() as db:
            row = db.execute("SELECT code FROM devices WHERE id=? AND expires>? AND consumed=0", (id, time.time())).fetchone()
        if not row:
            raise HTTPException(410, "登录请求已过期，请在客户端重新登录。")
        return {"code": row[0]}

    @app.post("/api/desktop/approve")
    async def approve(body: Approval, request: Request):
        session = p.auth(request)
        await p.wallet(session)
        # Store a separate session so desktop logout is independent of the browser.
        token = secrets.token_urlsafe(32)
        clone = p.Session(session.uid, p.new_client(), time.time()+30*86400)
        for c in session.client.cookies.jar:
            clone.client.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        p.save_session(token, clone)
        await clone.client.aclose()
        with p.database() as db:
            changed = db.execute("UPDATE devices SET approved=? WHERE id=? AND expires>? AND consumed=0 AND approved IS NULL", (token, body.id, time.time())).rowcount
            if not changed:
                db.execute("DELETE FROM sessions WHERE digest=?", (p.digest(token),))
                raise HTTPException(410, "请求已处理或过期，请重新发起登录。")
        return {"ok": True}

    @app.post("/api/desktop/poll")
    async def poll(body: Poll, request: Request):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(body.verifier.encode()).digest()).decode().rstrip("=")
        with p.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT secret,challenge,expires,approved,consumed FROM devices WHERE id=?", (body.id,)).fetchone()
            if not row or row[2] < time.time() or row[4]:
                raise HTTPException(410, "登录请求已过期，请重新登录。")
            if not secrets.compare_digest(row[0], p.digest(body.secret)) or not secrets.compare_digest(row[1], challenge):
                raise HTTPException(403, "登录校验失败。")
            if not row[3]:
                return {"status": "pending"}
            db.execute("UPDATE devices SET consumed=1,approved=NULL WHERE id=?", (body.id,))
        return {"status": "approved", "access_token": row[3], "expires_in": 30*86400}

    @app.get("/api/pricing")
    async def pricing():
        async with p.new_client() as client:
            r = await client.get("/api/pricing")
            r.raise_for_status()
            data = r.json()
        allowed = ("model_name", "quota_type", "model_ratio", "completion_ratio", "cache_ratio", "model_price", "enable_groups")
        return {"quota_per_point": p.QUOTA_PER_POINT, "models": [{k: m.get(k) for k in allowed} for m in data.get("data", [])],
                "group_ratio": data.get("group_ratio", {})}
