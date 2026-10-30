"""Root-owned Unix-socket helper. Only exposes creation of ordinary New API users."""
import asyncio
import json
import os
import time
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ConfigDict

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
lock = asyncio.Lock()
client = None
uid = None
expires = 0


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=20, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=10, max_length=20)


@app.post("/create")
async def create(body: Account):
    global client, uid, expires
    async with lock:
        try:
            if client is None or expires < time.time():
                if client:
                    await client.aclose()
                settings_path = Path(os.environ.get("PROVISIONER_SETTINGS", "/etc/starbridge-portal/provisioning.json"))
                config = json.loads(settings_path.read_text(encoding="utf-8"))
                admin = {"username": config["username"], "password": config["password"]}
                client = httpx.AsyncClient(base_url=config.get("upstream_url", "http://127.0.0.1:3000"), trust_env=False, timeout=20)
                r = await client.post("/api/user/login", json=admin)
                data = r.json()
                if not data.get("success") or not data.get("data", {}).get("id"):
                    expires = 0
                    raise HTTPException(503, "注册服务暂不可用。")
                uid = data["data"]["id"]
                expires = time.time()+600
            r = await client.post("/api/user/", headers={"New-Api-User": str(uid)}, json={
                "username": body.username, "password": body.password, "display_name": body.username, "role": 1})
            if r.status_code in (401, 403):
                expires = 0
                raise HTTPException(503, "注册服务暂不可用。")
            if not r.json().get("success"):
                raise HTTPException(400, "账号已存在或不符合要求；已有账号请直接登录。")
            return {"ok": True}
        except (httpx.RequestError, ValueError, KeyError):
            raise HTTPException(503, "注册服务暂不可用。") from None
