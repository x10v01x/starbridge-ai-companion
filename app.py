"""Customer BFF. No root credentials, direct New API database writes, or second wallet."""
from __future__ import annotations

import asyncio
import io
import json
import os
import secrets
import time
import zipfile
import hashlib
import sqlite3
import re
import ipaddress
from urllib.parse import urlsplit
from http.cookies import SimpleCookie
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DEFAULTS = json.loads((ROOT / "settings.example.json").read_text(encoding="utf-8-sig"))
settings_file = Path(os.environ.get("PORTAL_SETTINGS", str(ROOT / "settings.json")))
SETTINGS = DEFAULTS | (json.loads(settings_file.read_text(encoding="utf-8-sig")) if settings_file.exists() else {})
def validate_public_url(value, root_only=False):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("public URLs must be non-empty strings")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("invalid public URL") from exc
    if not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("public URLs cannot contain credentials, queries, or fragments")
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.lower() == "localhost"
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("public URLs require HTTPS except on loopback")
    if root_only and parsed.path not in ("", "/"):
        raise ValueError("public_url must be a root URL")
    return value.rstrip("/")


SETTINGS["public_url"] = validate_public_url(SETTINGS["public_url"], root_only=True)
SETTINGS["api_base_url"] = validate_public_url(SETTINGS["api_base_url"])
UPSTREAM = SETTINGS["upstream_url"].rstrip("/")
COOKIE = "starbridge_session"
KEY_NAME = "Starbridge WorkBuddy"
SESSION_SECONDS = max(300, min(86400, int(SETTINGS["session_hours"] * 3600)))
def quota_per_point(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10**12:
        raise ValueError("quota_per_point must be an integer between 1 and 1000000000000")
    return value


QUOTA_PER_POINT = quota_per_point(SETTINGS.get("quota_per_point", 5000))
USER_LOCKS = defaultdict(asyncio.Lock)


def database():
    path = Path(SETTINGS.get("state_path", str(ROOT / "state" / "portal.db")))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, uid INTEGER, cookies TEXT, expires REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, secret TEXT, challenge TEXT, code TEXT, expires REAL, approved TEXT, consumed INTEGER DEFAULT 0)")
    db.commit()
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return db


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def save_session(token, session):
    cookies = json.dumps([{k: getattr(c, k) for k in ("name", "value", "domain", "path")} for c in session.client.cookies.jar])
    with database() as db:
        db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?)", (digest(token), session.uid, cookies, session.expires))


def load_session(token):
    with database() as db:
        row = db.execute("SELECT uid,cookies,expires FROM sessions WHERE digest=? AND expires>?", (digest(token), time.time())).fetchone()
    if not row:
        return None
    client = new_client()
    for cookie in json.loads(row[1]):
        client.cookies.set(**cookie)
    return Session(row[0], client, row[2])


@dataclass
class Session:
    uid: int
    client: httpx.AsyncClient
    expires: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


sessions: dict[str, Session] = {}
login_attempts: dict[str, deque] = defaultdict(deque)
app = FastAPI(title="Starbridge customer portal", docs_url=None, redoc_url=None, openapi_url=None)


def new_client():
    return httpx.AsyncClient(base_url=UPSTREAM, timeout=20, follow_redirects=False, trust_env=False)


async def clean_sessions():
    now = time.time()
    for token, session in list(sessions.items()):
        if session.expires <= now:
            sessions.pop(token, None)
            await session.client.aclose()
    for ip, attempts in list(login_attempts.items()):
        while attempts and attempts[0] < now - 300:
            attempts.popleft()
        if not attempts:
            login_attempts.pop(ip, None)


@app.middleware("http")
async def protect(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        await clean_sessions()
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            # Cookie-auth writes must be same-origin. Native clients send the same origin.
            origin = request.headers.get("origin", "").rstrip("/")
            expected = str(request.base_url).rstrip("/")
            if origin != expected or request.headers.get("x-portal-request") != "1":
                return JSONResponse({"detail": "请求来源校验失败，请刷新页面后重试。"}, status_code=403)
            if "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse({"detail": "请使用 JSON 请求。"}, status_code=415)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 16384:
                return JSONResponse({"detail": "请求内容过大。"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith(("/api/", "/downloads/")):
        response.headers["Cache-Control"] = "no-store"
    if response.headers.get("content-type", "").startswith("application/json"):
        response.headers["content-type"] = "application/json; charset=utf-8"
    return response


def auth(request: Request) -> Session:
    token = request.headers.get("authorization", "").removeprefix("Bearer ") if request.headers.get("authorization", "").startswith("Bearer ") else request.cookies.get(COOKIE, "")
    session = sessions.get(token)
    if not session and token:
        session = load_session(token)
        if session:
            sessions[token] = session
    if not session or session.expires <= time.time():
        raise HTTPException(401, "登录已过期，请重新登录。")
    return session


async def upstream(session: Session, method: str, path: str, body=None):
    try:
        response = await session.client.request(method, path, json=body, headers={"New-Api-User": str(session.uid)})
    except httpx.RequestError:
        raise HTTPException(502, "暂时无法连接账户服务，请稍后重试。") from None
    if response.status_code in (401, 403):
        raise HTTPException(401, "账户登录已失效，请重新登录。")
    if response.status_code == 429:
        raise HTTPException(429, "操作较频繁，请稍后重试。")
    try:
        data = response.json()
    except ValueError:
        raise HTTPException(502, "账户服务返回异常，请联系服务方。") from None
    if response.status_code >= 400 or data.get("success") is False:
        # Never forward an entire upstream body, which may contain credential data.
        message = str(data.get("message") or "操作未完成，请稍后重试。")[:200]
        raise HTTPException(400 if response.status_code < 500 else 502, message)
    return data.get("data", data)


async def wallet(session: Session):
    data = await upstream(session, "GET", "/api/user/self")
    if data.get("status") != 1:
        raise HTTPException(403, "账号已停用，请联系服务方。")
    return {
        "id": data["id"], "username": data["username"],
        "is_admin": data.get("role", 1) >= 10,
        "display_name": data.get("display_name") or data["username"],
        "balance": data.get("quota", 0), "used": data.get("used_quota", 0),
        "points": data.get("quota", 0) / QUOTA_PER_POINT,
        "used_points": data.get("used_quota", 0) / QUOTA_PER_POINT,
        "quota_per_point": QUOTA_PER_POINT,
        "can_call": data.get("quota", 0) > 0,
        "requests": data.get("request_count", 0), "updated_at": int(time.time()),
    }


async def available_models(session: Session):
    raw = await upstream(session, "GET", "/api/user/models")
    if not isinstance(raw, list):
        raise HTTPException(502, "模型列表暂不可用。")
    capabilities_file = ROOT / "model-capabilities.json"
    capabilities = json.loads(capabilities_file.read_text(encoding="utf-8-sig")) if capabilities_file.exists() else {}
    result = []
    for entry in raw:
        model_id = entry if isinstance(entry, str) else entry.get("id")
        if not model_id or not isinstance(model_id, str):
            continue
        meta = capabilities.get(model_id, {})
        bare = model_id.removeprefix("cn:").removeprefix("global:")
        name = meta.get("name") or bare
        result.append({"id": model_id, "name": name, "description": meta.get("description", ""),
                       "tools": bool(meta.get("supports_tool_call", False)),
                       "vision": bool(meta.get("supports_images", False)),
                       "context": meta.get("context_length"), "max_output": meta.get("max_output_tokens"),
                       "recommended": bool(meta.get("recommended", False))})
    return sorted(result, key=lambda m: (not m["recommended"], m["id"]))


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(default="", max_length=32)
    remember: bool = True


class Redeem(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class Configure(BaseModel):
    models: list[str] = Field(min_length=1, max_length=64)


@app.get("/api/public")
async def public():
    return {"brand": SETTINGS["brand"], "test_pricing": SETTINGS["test_pricing"],
            "refresh_seconds": SETTINGS["refresh_seconds"], "support_text": SETTINGS["support_text"],
            "api_base_url": SETTINGS["api_base_url"], "payment_enabled": False, "registration_enabled": bool(SETTINGS.get("registration_enabled", False)), "quota_per_point": QUOTA_PER_POINT}


@app.get("/api/health")
async def health():
    try:
        async with new_client() as client:
            response = await client.get("/api/status")
            ok = response.status_code == 200 and response.json().get("success") is True
    except (httpx.RequestError, ValueError):
        ok = False
    return JSONResponse({"ok": ok, "service": "starbridge-customer-portal"}, status_code=200 if ok else 503)


@app.post("/api/login")
async def login(body: Login, request: Request):
    ip = request.client.host if request.client else "unknown"
    attempts = login_attempts[ip]
    if len(attempts) >= 8:
        raise HTTPException(429, "登录尝试过多，请五分钟后重试。")
    attempts.append(time.time())
    if len(sessions) >= 1000:
        raise HTTPException(503, "当前登录人数较多，请稍后重试。")
    client = new_client()
    try:
        response = await client.post("/api/user/login", json={"username": body.username.strip(), "password": body.password})
        data = response.json()
        if data.get("success") is not True:
            raise HTTPException(401, "账号或密码不正确，或账号暂不可用。")
        if data.get("data", {}).get("require_2fa"):
            if not body.code:
                return {"requires_2fa": True}
            response = await client.post("/api/user/login/2fa", json={"code": body.code})
            data = response.json()
            if data.get("success") is not True:
                raise HTTPException(401, "二次验证码不正确，请重试。")
        uid = int(data["data"]["id"])
        session = Session(uid, client, time.time() + (30 * 86400 if body.remember else SESSION_SECONDS))
        profile = await wallet(session)
    except HTTPException:
        await client.aclose()
        raise
    except (httpx.RequestError, ValueError, KeyError, TypeError):
        await client.aclose()
        raise HTTPException(502, "登录服务暂不可用，请稍后重试。") from None
    finally:
        if 'data' in locals() and data.get("data", {}).get("require_2fa") and not body.code:
            await client.aclose()
    token = secrets.token_urlsafe(32)
    sessions[token] = session
    save_session(token, session)
    old = sessions.pop(request.cookies.get(COOKIE, ""), None)
    if old:
        await old.client.aclose()
    response = JSONResponse({"user": profile, "requires_2fa": False})
    response.set_cookie(COOKIE, token, httponly=True, secure=bool(SETTINGS["secure_cookie"]),
                        samesite="strict", max_age=int(session.expires - time.time()), path="/")
    return response


@app.post("/api/logout")
async def logout(request: Request):
    token = request.headers.get("authorization", "").removeprefix("Bearer ") if request.headers.get("authorization", "").startswith("Bearer ") else request.cookies.get(COOKIE, "")
    with database() as db:
        db.execute("DELETE FROM sessions WHERE digest=?", (digest(token),))
    session = sessions.pop(token, None)
    if session:
        try:
            await session.client.get("/api/user/logout")
        except httpx.RequestError:
            pass
        await session.client.aclose()
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@app.get("/api/me")
async def me(request: Request):
    return await wallet(auth(request))


@app.get("/api/models")
async def models(request: Request):
    session = auth(request)
    await wallet(session)
    return {"models": await available_models(session)}


@app.get("/api/usage")
async def usage(request: Request, page: int = 1):
    session = auth(request)
    await wallet(session)
    page = max(1, min(page, 10000))
    raw = await upstream(session, "GET", f"/api/log/self?p={page}&page_size=10")
    items = raw.get("items", [])
    return {"total": raw.get("total", len(items)), "page": page, "items": [
        {k: entry.get(k) for k in ("id", "type", "created_at", "model_name", "quota", "prompt_tokens", "completion_tokens", "is_stream")}
        for entry in items]}


@app.post("/api/redeem")
async def redeem(body: Redeem, request: Request):
    session = auth(request)
    async with session.lock:
        await wallet(session)
        credited = await upstream(session, "POST", "/api/user/topup", {"key": body.code.strip()})
        return {"credited": credited, "user": await wallet(session)}


@app.post("/api/configuration")
async def configuration(body: Configure, request: Request):
    session = auth(request)
    return await build_configuration(session, body.models)


async def build_configuration(session, model_ids):
    async with USER_LOCKS[session.uid]:
        await wallet(session)
        all_models = await available_models(session)
        selected_ids = set(model_ids)
        selected = [m for m in all_models if m["id"] in selected_ids]
        if len(selected) != len(selected_ids):
            raise HTTPException(400, "部分模型已不可用，请刷新模型列表。")
        # This upstream user session can only create/read this user's own keys.
        # Use one portal-managed key, rather than creating a new key per click.
        found = None
        page = 1
        while page <= 100:
            listing = await upstream(session, "GET", f"/api/token/?p={page}&page_size=100")
            for token in listing.get("items", []):
                if token.get("name") == KEY_NAME and token.get("status") == 1 and token.get("expired_time", -1) == -1 and token.get("unlimited_quota") and not token.get("model_limits_enabled") and token.get("group", "") == "":
                    found = token
                    break
            if found or page * 100 >= listing.get("total", 0):
                break
            page += 1
        if not found:
            await upstream(session, "POST", "/api/token/", {"name": KEY_NAME, "expired_time": -1,
                "unlimited_quota": True, "remain_quota": 0, "model_limits_enabled": False, "group": ""})
            listing = await upstream(session, "GET", "/api/token/?p=1&page_size=100")
            found = next((t for t in listing.get("items", []) if t.get("name") == KEY_NAME and t.get("status") == 1), None)
        if not found:
            raise HTTPException(502, "专属凭证创建失败，请重试。")
        key = (await upstream(session, "POST", f"/api/token/{int(found['id'])}/key"))["key"]
        if not key.startswith("sk-"):
            key = "sk-" + key
        api_base = SETTINGS["api_base_url"].rstrip("/")
        result = []
        for model in selected:
            item = {"id": model["id"], "name": SETTINGS["brand"] + " · " + model["name"],
                    "vendor": "Custom", "url": api_base + "/chat/completions", "apiKey": key,
                    "supportsToolCall": model["tools"], "supportsImages": model["vision"]}
            if isinstance(model["context"], int) and model["context"] > 0:
                item["maxInputTokens"] = model["context"]
            if isinstance(model["max_output"], int) and model["max_output"] > 0:
                item["maxOutputTokens"] = model["max_output"]
            result.append(item)
        return {"brand": SETTINGS["brand"], "api_base_url": api_base, "models": result,
                "key_id": found["id"], "note": "专属凭证受账户钱包余额限制；无限 Key 预算不等于无限账户余额。"}


@app.get("/downloads/Starbridge-Assistant.exe")
async def exe_download():
    path = ROOT / "dist" / "Starbridge-Assistant.exe"
    if not path.exists():
        raise HTTPException(503, "客户端正在构建，请稍后重试。")
    return FileResponse(path, media_type="application/octet-stream", filename="Starbridge-Assistant.exe")


@app.get("/downloads/windows.zip")
async def windows_download(request: Request):
    data = io.BytesIO()
    profile = {"brand": SETTINGS["brand"], "portal_url": SETTINGS["public_url"].rstrip("/")}
    exe = ROOT / "dist" / "Starbridge-Assistant.exe"
    if exe.exists():
        with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(exe, "Starbridge-Assistant.exe")
            archive.writestr("portal-profile.json", json.dumps(profile, ensure_ascii=False).encode("utf-8"))
            archive.writestr("README.txt", "星桥 AI Windows 助手 2.0\n双击 EXE，在浏览器登录或注册，核对验证码并允许登录，再返回助手选择模型。\n配置前自动备份。配置含个人 Key，请勿分享。\n".encode("utf-8-sig"))
        return Response(data.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="Starbridge-WorkBuddy-Windows.zip"'})
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("Start.cmd", "Starbridge-Assistant.ps1", "README.txt"):
            content = (ROOT / "configurator" / name).read_bytes()
            # Windows PowerShell 5.1 treats BOM-less scripts as the system ANSI codepage.
            if name.endswith(".ps1") and not content.startswith(b"\xef\xbb\xbf"):
                content = b"\xef\xbb\xbf" + content
            if name.endswith(".cmd"):
                content = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            archive.writestr(name, content)
        archive.writestr("portal-profile.json", json.dumps(profile, ensure_ascii=False, indent=2).encode("utf-8"))
    return Response(data.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="Starbridge-WorkBuddy-Windows.zip"'})


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


import sys
from identity import install as install_identity
install_identity(sys.modules[__name__])
