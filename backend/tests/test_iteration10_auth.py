"""Iteration 10 — Email & Password Login integration (auth focused)."""
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

fe = dotenv_values("/app/frontend/.env")
be = dotenv_values("/app/backend/.env")
BASE = (os.environ.get("REACT_APP_BACKEND_URL") or fe.get("REACT_APP_BACKEND_URL", "")).rstrip("/")
API = f"{BASE}/api"
if not BASE:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")

MONGO_URL = be.get("MONGO_URL")
DB_NAME = be.get("DB_NAME")
DEMO_PW = "CareFlow123!"
XFF = {"X-Forwarded-For": "203.0.113.5"}


@pytest.fixture(scope="session")
def mongo():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


def s():
    ses = requests.Session()
    ses.headers.update({"Content-Type": "application/json", **XFF})
    return ses


# ---------- health sanity ----------
def test_health():
    r = requests.get(f"{API}/health", timeout=30)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "healthy"


# ---------- existing login sanity ----------
@pytest.mark.parametrize("email,role", [
    ("patient@careflow.demo", "patient"),
    ("doctor1@careflow.demo", "doctor"),
    ("admin@careflow.demo", "admin"),
])
def test_demo_login_and_me(email, role):
    ses = s()
    r = ses.post(f"{API}/auth/login", json={"email": email, "password": DEMO_PW})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == email
    assert r.json()["role"] == role
    assert "password_hash" not in r.json()
    assert "session_token" in ses.cookies.get_dict(), "session cookie not set"
    me = ses.get(f"{API}/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == role


def test_bcrypt_hash_format(mongo):
    u = mongo.users.find_one({"email": "patient@careflow.demo"})
    assert u and u.get("password_hash", "").startswith("$2b$"), u.get("password_hash", "")[:6]


def test_me_requires_auth():
    r = requests.get(f"{API}/auth/me", timeout=30)
    assert r.status_code == 401


# ---------- register ----------
class TestRegister:
    def test_weak_password_rejected(self):
        r = s().post(f"{API}/auth/register", json={
            "name": "TEST Weak", "email": f"TEST_weak_{uuid.uuid4().hex[:8]}@careflow.demo",
            "password": "short1", "role": "patient"})
        assert r.status_code == 400, r.text
        assert "at least 8 characters" in r.json().get("detail", "")

    def test_register_success_and_duplicate(self, mongo):
        email = f"TEST_reg_{uuid.uuid4().hex[:8]}@careflow.demo"
        ses = s()
        r = ses.post(f"{API}/auth/register", json={
            "name": "TEST Reg", "email": email, "password": "StrongPass1!", "role": "patient"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == email.lower()
        assert body["role"] == "patient"
        assert "password_hash" not in body
        assert "session_token" in ses.cookies.get_dict()
        assert ses.get(f"{API}/auth/me").json()["email"] == email.lower()

        dup = s().post(f"{API}/auth/register", json={
            "name": "TEST Dup", "email": email, "password": "StrongPass1!", "role": "patient"})
        assert dup.status_code == 409, dup.text
        assert "already exists" in dup.json().get("detail", "")

        mongo.users.delete_many({"email": email.lower()})
        mongo.notifications.delete_many({"user_id": body["user_id"]})


# ---------- forgot / reset ----------
class TestForgotReset:
    @pytest.fixture(scope="class")
    def user(self, mongo):
        email = f"test_reset_{uuid.uuid4().hex[:8]}@careflow.demo"
        ses = s()
        r = ses.post(f"{API}/auth/register", json={
            "name": "TEST Reset", "email": email, "password": "StrongPass1!", "role": "patient"})
        assert r.status_code == 200, r.text
        info = {"email": email, "password": "StrongPass1!", "user_id": r.json()["user_id"]}
        yield info
        mongo.users.delete_many({"email": email})
        mongo.notifications.delete_many({"user_id": info["user_id"]})
        mongo.password_reset_tokens.delete_many({"user_id": info["user_id"]})

    def _token(self, user):
        ses = s()
        lr = ses.post(f"{API}/auth/login", json={"email": user["email"], "password": user["password"]})
        assert lr.status_code == 200, lr.text
        notes = ses.get(f"{API}/notifications")
        assert notes.status_code == 200
        for n in notes.json():
            if n.get("kind") == "auth" and "/reset-password?token=" in n.get("message", ""):
                return re.search(r"/reset-password\?token=([A-Za-z0-9_\-]+)", n["message"]).group(1)
        pytest.fail(f"no auth notification with reset token: {notes.json()}")

    def test_forgot_unknown_email_no_leak(self, mongo):
        before = mongo.password_reset_tokens.count_documents({})
        r = s().post(f"{API}/auth/forgot-password", json={"email": "nobody_here@careflow.demo"})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        assert mongo.password_reset_tokens.count_documents({}) == before

    def test_forgot_known_email_creates_token_and_notification(self, user, mongo):
        r = s().post(f"{API}/auth/forgot-password", json={"email": user["email"]})
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert mongo.password_reset_tokens.count_documents({"user_id": user["user_id"], "used": False}) >= 1
        tok = self._token(user)
        assert len(tok) > 20
        assert mongo.password_reset_tokens.find_one({"token": tok, "user_id": user["user_id"]})

    def test_reset_invalid_token(self):
        r = s().post(f"{API}/auth/reset-password", json={"token": "not-a-real-token", "password": "StrongPass2!"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "Invalid or expired reset link"

    def test_reset_expired_token(self, user, mongo):
        assert s().post(f"{API}/auth/forgot-password", json={"email": user["email"]}).status_code == 200
        tok = self._token(user)
        mongo.password_reset_tokens.update_one(
            {"token": tok}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(hours=2)}})
        r = s().post(f"{API}/auth/reset-password", json={"token": tok, "password": "StrongPass2!"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "Reset link has expired"
        mongo.password_reset_tokens.delete_one({"token": tok})
        mongo.notifications.delete_many({"user_id": user["user_id"], "kind": "auth"})

    def test_reset_weak_password_then_valid_then_reuse(self, user, mongo):
        assert s().post(f"{API}/auth/forgot-password", json={"email": user["email"]}).status_code == 200
        tok = self._token(user)

        weak = s().post(f"{API}/auth/reset-password", json={"token": tok, "password": "abc123"})
        assert weak.status_code == 400, weak.text
        assert "at least 8 characters" in weak.json()["detail"]

        new_pw = "BrandNewPass9!"
        ok = s().post(f"{API}/auth/reset-password", json={"token": tok, "password": new_pw})
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"ok": True}
        assert mongo.password_reset_tokens.find_one({"token": tok})["used"] is True

        # new password works, old one fails
        ses = s()
        assert ses.post(f"{API}/auth/login", json={"email": user["email"], "password": new_pw}).status_code == 200
        old = s().post(f"{API}/auth/login", json={"email": user["email"], "password": "StrongPass1!"})
        assert old.status_code == 401, old.text
        user["password"] = new_pw

        reuse = s().post(f"{API}/auth/reset-password", json={"token": tok, "password": "AnotherPass9!"})
        assert reuse.status_code == 400, reuse.text
        # cleanup lockout record created by the intentional wrong-password login
        mongo.login_attempts.delete_many({"identifier": f"203.0.113.5:{user['email']}"})


# ---------- brute-force lockout ----------
class TestLockout:
    def test_lockout_after_five_failures_and_reset_on_success(self, mongo):
        email = f"test_lock_{uuid.uuid4().hex[:8]}@careflow.demo"
        pw = "LockPass123!"
        reg = s().post(f"{API}/auth/register", json={"name": "TEST Lock", "email": email, "password": pw, "role": "patient"})
        assert reg.status_code == 200, reg.text
        uid = reg.json()["user_id"]
        ident = f"203.0.113.5:{email}"
        try:
            ses = s()
            codes = []
            for _ in range(5):
                codes.append(ses.post(f"{API}/auth/login", json={"email": email, "password": "wrongpass"}).status_code)
            assert codes == [401] * 5, codes
            sixth = ses.post(f"{API}/auth/login", json={"email": email, "password": "wrongpass"})
            assert sixth.status_code == 429, f"{sixth.status_code} {sixth.text}"
            assert "Too many failed sign-in attempts" in sixth.json()["detail"]
            # correct password is also locked out while window is active
            locked = ses.post(f"{API}/auth/login", json={"email": email, "password": pw})
            assert locked.status_code == 429, locked.status_code
            # clear counter, then successful login must clear the record
            mongo.login_attempts.delete_many({"identifier": ident})
            good = ses.post(f"{API}/auth/login", json={"email": email, "password": pw})
            assert good.status_code == 200, good.text
            assert mongo.login_attempts.find_one({"identifier": ident}) is None
        finally:
            mongo.users.delete_many({"email": email})
            mongo.login_attempts.delete_many({"identifier": ident})
            mongo.notifications.delete_many({"user_id": uid})


# ---------- indexes ----------
def test_mongo_indexes(mongo):
    ui = mongo.users.index_information()
    assert "email_1" in ui and ui["email_1"].get("unique") is True, ui
    pi = mongo.password_reset_tokens.index_information()
    assert any(v.get("expireAfterSeconds") is not None and v["key"][0][0] == "expires_at" for v in pi.values()), pi
    assert "token_1" in pi and pi["token_1"].get("unique") is True, pi
    li = mongo.login_attempts.index_information()
    assert "identifier_1" in li and li["identifier_1"].get("unique") is True, li


# ---------- demo credentials integrity (must stay CareFlow123!) ----------
def test_demo_credentials_intact_at_end():
    for email in ["patient@careflow.demo", "doctor1@careflow.demo", "admin@careflow.demo"]:
        r = s().post(f"{API}/auth/login", json={"email": email, "password": DEMO_PW})
        assert r.status_code == 200, f"{email}: {r.status_code} {r.text}"
