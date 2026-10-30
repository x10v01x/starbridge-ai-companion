"""Contract/security tests against a faithful, isolated New API simulator."""
import io
import json
import sys
import unittest
import zipfile
import tempfile
import hashlib
import base64
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as portal


class FakeNewAPI:
    def __init__(self):
        self.users = {1: {"username": "alice", "quota": 1000, "used_quota": 12, "request_count": 1},
                      2: {"username": "bob", "quota": 77, "used_quota": 0, "request_count": 0}}
        self.tokens = {1: [], 2: []}
        self.calls = []
        self.redeemed = False

    def handle(self, request):
        path = request.url.path
        body = json.loads(request.content or b"{}")
        self.calls.append((request.method, path, dict(request.headers), body))
        uid = 1 if "upstream=alice" in request.headers.get("cookie", "") else 2 if "upstream=bob" in request.headers.get("cookie", "") else 0
        if path == "/api/user/login":
            uid = next((i for i, u in self.users.items() if u["username"] == body.get("username")), 0)
            if body.get("password") != "test-pass" or not uid:
                return httpx.Response(200, json={"success": False, "message": "bad credentials"})
            return httpx.Response(200, json={"success": True, "data": {"id": uid}}, headers={"Set-Cookie": f"upstream={body['username']}; Path=/; HttpOnly"})
        if path == "/api/status":
            return httpx.Response(200, json={"success": True})
        if path == "/api/user/logout":
            return httpx.Response(200, json={"success": True})
        if not uid or str(uid) != request.headers.get("new-api-user"):
            return httpx.Response(401, json={"success": False})
        if path == "/api/user/self":
            return self.ok({"id": uid, "status": 1, **self.users[uid]})
        if path == "/api/user/models":
            return self.ok(["cn:glm-5.3-flash"] if uid == 1 else ["bob-only-model"])
        if path == "/api/user/topup":
            if body.get("key") != "TEST-CODE" or self.redeemed:
                return httpx.Response(200, json={"success": False, "message": "兑换码无效或已使用"})
            self.redeemed = True
            self.users[uid]["quota"] += 500
            return self.ok(500)
        if path == "/api/token/":
            if request.method == "POST":
                self.tokens[uid].append({"id": uid * 10, "status": 1, **body})
                return self.ok(None)
            return self.ok({"items": self.tokens[uid], "total": len(self.tokens[uid])})
        if path == f"/api/token/{uid * 10}/key":
            return self.ok({"key": "FAKE-KEY-FOR-" + self.users[uid]["username"]})
        if path == "/api/log/self":
            return self.ok({"total": 1, "items": [{"id": uid, "type": 2, "model_name": "test", "quota": 12, "channel": 999, "other": "private metadata", "token_name": "PRIVATE-NAME"}]})
        return httpx.Response(404, json={"success": False, "message": "not found"})

    @staticmethod
    def ok(data):
        return httpx.Response(200, json={"success": True, "data": data})


class PortalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        portal.sessions.clear()
        portal.SETTINGS["state_path"] = str(Path(tempfile.mkdtemp(prefix="starbridge-test-")) / "state.db")
        portal.login_attempts.clear()
        self.fake = FakeNewAPI()
        self.patch = patch.object(portal, "new_client", lambda: httpx.AsyncClient(base_url="http://upstream", transport=httpx.MockTransport(self.fake.handle)))
        self.patch.start()
        self.browser = httpx.AsyncClient(transport=httpx.ASGITransport(app=portal.app), base_url="http://test")
        self.other = httpx.AsyncClient(transport=httpx.ASGITransport(app=portal.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.browser.aclose()
        await self.other.aclose()
        for s in portal.sessions.values():
            await s.client.aclose()
        portal.sessions.clear()
        self.patch.stop()

    async def post(self, path, body, browser=None):
        return await (browser or self.browser).post(path, json=body, headers={"Origin": "http://test", "X-Portal-Request": "1"})

    async def login(self, name="alice", browser=None):
        result = await self.post("/api/login", {"username": name, "password": "test-pass"}, browser)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn("httponly", result.headers["set-cookie"].lower())
        self.assertNotIn("FAKE-KEY", result.text)

    async def test_auth_csrf_and_login_rate_limit(self):
        self.assertEqual((await self.browser.get("/api/me")).status_code, 401)
        self.assertEqual((await self.browser.post("/api/login", json={"username": "alice", "password": "test-pass"})).status_code, 403)
        self.assertEqual((await self.browser.post("/api/login", json={"username": "alice", "password": "test-pass"}, headers={"Origin": "http://evil", "X-Portal-Request": "1"})).status_code, 403)
        for _ in range(8):
            self.assertEqual((await self.post("/api/login", {"username": "alice", "password": "bad"})).status_code, 401)
        self.assertEqual((await self.post("/api/login", {"username": "alice", "password": "bad"})).status_code, 429)

    async def test_wallet_is_actual_user_wallet_and_never_another_user(self):
        await self.login()
        await self.login("bob", self.other)
        a = (await self.browser.get("/api/me")).json()
        b = (await self.other.get("/api/me")).json()
        self.assertEqual(a["balance"], 1000)
        self.assertEqual(b["balance"], 77)
        self.assertNotIn("password", a)
        self.assertNotIn("upstream", a)

    async def test_redeem_exactly_once_and_returns_authoritative_balance(self):
        await self.login()
        result = await self.post("/api/redeem", {"code": "TEST-CODE"})
        self.assertEqual(result.json()["user"]["balance"], 1500)
        self.assertEqual(result.json()["credited"], 500)
        self.assertEqual((await self.post("/api/redeem", {"code": "TEST-CODE"})).status_code, 400)
        self.assertEqual((await self.browser.get("/api/me")).json()["balance"], 1500)

    async def test_configuration_scope_and_key_reuse(self):
        await self.login()
        self.assertEqual((await self.post("/api/configuration", {"models": ["bob-only-model"]})).status_code, 400)
        first = await self.post("/api/configuration", {"models": ["cn:glm-5.3-flash"]})
        second = await self.post("/api/configuration", {"models": ["cn:glm-5.3-flash"]})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(len(self.fake.tokens[1]), 1)
        self.assertEqual(len(self.fake.tokens[2]), 0)
        self.assertEqual(first.json()["models"][0]["apiKey"], "sk-FAKE-KEY-FOR-alice")
        self.assertEqual(first.json()["key_id"], second.json()["key_id"])
        self.assertEqual(first.headers["cache-control"], "no-store")
        self.assertTrue(first.json()["models"][0]["url"].endswith("/v1/chat/completions"))

    async def test_usage_redacts_internal_upstream_metadata(self):
        await self.login()
        response = await self.browser.get("/api/usage")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("PRIVATE", response.text)
        self.assertNotIn("private metadata", response.text)
        self.assertNotIn('"channel"', response.text)

    async def test_installer_has_no_private_keys_and_supports_powershell_51(self):
        response = await self.browser.get("/downloads/windows.zip")
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            if "Starbridge-Assistant.exe" in archive.namelist():
                self.assertTrue(archive.read("Starbridge-Assistant.exe").startswith(b"MZ"))
                self.assertEqual(set(archive.namelist()), {"Starbridge-Assistant.exe", "README.txt", "portal-profile.json"})
                profile = json.loads(archive.read("portal-profile.json"))
                self.assertEqual(profile["portal_url"], portal.SETTINGS["public_url"])
                self.assertNotIn("password", profile)
                self.assertNotIn("api_key", profile)
                return
            self.assertEqual(set(archive.namelist()), {"Start.cmd", "Starbridge-Assistant.ps1", "README.txt", "portal-profile.json"})
            self.assertTrue(archive.read("Starbridge-Assistant.ps1").startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"FAKE-KEY", response.content)
            profile = json.loads(archive.read("portal-profile.json"))
            self.assertNotIn("password", profile)
            self.assertNotIn("api_key", profile)

    async def test_logout_invalidates_local_session(self):
        await self.login()
        self.assertEqual((await self.post("/api/logout", {})).status_code, 200)
        self.assertEqual((await self.browser.get("/api/me")).status_code, 401)

    async def test_public_url_validation(self):
        self.assertEqual(portal.validate_public_url("https://api.example.com/v1"), "https://api.example.com/v1")
        self.assertEqual(portal.validate_public_url("http://127.0.0.1:8400/", root_only=True), "http://127.0.0.1:8400")
        for invalid in ("http://example.com", "https://user@example.com", "https://example.com/?token=bad", "https://example.com/#fragment"):
            with self.assertRaises(ValueError):
                portal.validate_public_url(invalid)
        with self.assertRaises(ValueError):
            portal.validate_public_url("https://example.com/path", root_only=True)

    async def test_configurable_quota_conversion(self):
        self.assertEqual(portal.quota_per_point(10000), 10000)
        for invalid in (0, -1, True, 1.5, "5000", 10**12 + 1):
            with self.assertRaises(ValueError):
                portal.quota_per_point(invalid)
        await self.login()
        with patch.object(portal, "QUOTA_PER_POINT", 10000):
            user = (await self.browser.get("/api/me")).json()
            config = (await self.browser.get("/api/public")).json()
            self.assertEqual(user["points"], 0.1)
            self.assertEqual(config["quota_per_point"], 10000)

    async def test_persistent_login_points_and_revoke(self):
        await self.login()
        for session in portal.sessions.values():
            await session.client.aclose()
        portal.sessions.clear()
        user = (await self.browser.get("/api/me")).json()
        self.assertEqual(user["points"], 0.2)
        self.assertEqual(user["quota_per_point"], 5000)
        await self.post("/api/logout", {})
        portal.sessions.clear()
        self.assertEqual((await self.browser.get("/api/me")).status_code, 401)

    async def test_desktop_pkce_browser_consent_single_use_and_logout(self):
        verifier = "a" * 43
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        started = (await self.post("/api/desktop/start", {"challenge": challenge})).json()
        payload = {"id": started["id"], "secret": started["secret"], "verifier": verifier}
        self.assertEqual((await self.post("/api/desktop/poll", payload)).json()["status"], "pending")
        self.assertNotIn(started["secret"], started["verification_url"])
        self.assertEqual((await self.post("/api/desktop/approve", {"id": started["id"]})).status_code, 401)
        await self.login()
        self.assertEqual((await self.post("/api/desktop/approve", {"id": started["id"]})).status_code, 200)
        wrong = dict(payload, verifier="b" * 43)
        self.assertEqual((await self.post("/api/desktop/poll", wrong)).status_code, 403)
        result = (await self.post("/api/desktop/poll", payload)).json()
        self.assertEqual(result["status"], "approved")
        self.assertEqual((await self.post("/api/desktop/poll", payload)).status_code, 410)
        headers = {"Authorization": "Bearer " + result["access_token"]}
        self.assertEqual((await self.other.get("/api/me", headers=headers)).json()["username"], "alice")
        await self.post("/api/logout", {})
        self.assertEqual((await self.other.get("/api/me", headers=headers)).status_code, 200)
        await self.other.post("/api/logout", json={}, headers={**headers, "Origin": "http://test", "X-Portal-Request": "1"})
        self.assertEqual((await self.other.get("/api/me", headers=headers)).status_code, 401)


if __name__ == "__main__":
    unittest.main()
