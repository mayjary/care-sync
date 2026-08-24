"""Iteration 11 targeted retest:
(a) brute-force lockout counter resets once the 15-minute window has lapsed
(b) sessions invalidated after password reset (password_changed_at vs token iat)
plus a small regression sanity check on seeded demo accounts.
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

frontend_env = dotenv_values("/app/frontend/.env")
backend_env = dotenv_values("/app/backend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/") + "/api"

MONGO_URL = os.environ.get("MONGO_URL") or backend_env.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME") or backend_env.get("DB_NAME")

LOCK_EMAIL = "lockreset+iter11@careflow.demo"
RESET_EMAIL = "test_sessionreset+iter11@careflow.demo"
PASSWORD = "Str0ngPass!"
NEW_PASSWORD = "N3wStr0ngPass!"
FIXED_IP = "203.0.113.9"
LOCK_IDENT = f"{FIXED_IP}:{LOCK_EMAIL.lower()}"


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


def _purge_user(mongo, email):
    """Remove a test user plus its notifications/reset tokens."""
    u = mongo.users.find_one({"email": email.lower()})
    if u:
        mongo.notifications.delete_many({"user_id": u["user_id"]})
        mongo.password_reset_tokens.delete_many({"user_id": u["user_id"]})
        mongo.users.delete_one({"user_id": u["user_id"]})


# NOTE: cleanup is intentionally class-scoped and identifier-exact. A module-scoped
# autouse cleanup races with xdist workers and wipes login_attempts mid-test.
@pytest.fixture(scope="class")
def lock_cleanup(mongo):
    yield
    _purge_user(mongo, LOCK_EMAIL)
    mongo.login_attempts.delete_many({"identifier": LOCK_IDENT})


@pytest.fixture(scope="class")
def reset_cleanup(mongo):
    yield
    _purge_user(mongo, RESET_EMAIL)


def register(email, password, session=None, headers=None):
    s = session or requests.Session()
    r = s.post(f"{BASE_URL}/auth/register", json={"name": "Iter11 Tester", "email": email, "password": password},
               headers=headers or {}, timeout=30)
    return s, r


# ---------- Regression sanity ----------
class TestRegressionSanity:
    def test_health(self):
        r = requests.get(f"{BASE_URL}/health", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "healthy"
        assert body["database"] == "healthy"

    @pytest.mark.parametrize("email,role", [
        ("patient@careflow.demo", "patient"),
        ("doctor1@careflow.demo", "doctor"),
        ("admin@careflow.demo", "admin"),
    ])
    def test_demo_login_and_me(self, email, role):
        s = requests.Session()
        r = s.post(f"{BASE_URL}/auth/login", json={"email": email, "password": "CareFlow123!"}, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["role"] == role
        assert "session_token" in s.cookies
        me = s.get(f"{BASE_URL}/auth/me", timeout=30)
        assert me.status_code == 200, me.text
        assert me.json()["email"] == email
        assert me.json()["role"] == role


# ---------- (a) Lockout window reset ----------
class TestLockoutWindowReset:
    def test_lockout_counter_resets_after_window(self, mongo, lock_cleanup):
        mongo.users.delete_one({"email": LOCK_EMAIL.lower()})
        mongo.login_attempts.delete_many({"identifier": LOCK_IDENT})
        headers = {"X-Forwarded-For": FIXED_IP}
        s, r = register(LOCK_EMAIL, PASSWORD, headers=headers)
        assert r.status_code == 200, r.text
        uid = r.json()["user_id"]
        assert mongo.users.find_one({"user_id": uid}) is not None

        # Seed a lapsed lockout row: count at max, last = 20 minutes ago
        stale = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        mongo.login_attempts.update_one({"identifier": LOCK_IDENT},
                                        {"$set": {"count": 5, "last": stale}}, upsert=True)

        # FIRST wrong attempt after the window lapsed must be 401, not 429
        r1 = s.post(f"{BASE_URL}/auth/login", json={"email": LOCK_EMAIL, "password": "wrong-pass-1"},
                    headers=headers, timeout=30)
        assert r1.status_code == 401, f"expected 401 after window lapse, got {r1.status_code}: {r1.text}"

        doc = mongo.login_attempts.find_one({"identifier": LOCK_IDENT})
        assert doc is not None
        assert doc["count"] == 1, f"counter should recount to 1, got {doc['count']}"

        # 4 more wrong attempts stay 401 (counts 2..5)
        for i in range(2, 6):
            ri = s.post(f"{BASE_URL}/auth/login", json={"email": LOCK_EMAIL, "password": f"wrong-pass-{i}"},
                        headers=headers, timeout=30)
            assert ri.status_code == 401, f"attempt {i} expected 401 got {ri.status_code}: {ri.text}"
        assert mongo.login_attempts.find_one({"identifier": LOCK_IDENT})["count"] == 5

        # 6th attempt within the fresh window must be locked out
        r6 = s.post(f"{BASE_URL}/auth/login", json={"email": LOCK_EMAIL, "password": "wrong-pass-6"},
                    headers=headers, timeout=30)
        assert r6.status_code == 429, f"expected 429 on 6th attempt, got {r6.status_code}: {r6.text}"
        assert "Too many failed" in r6.json().get("detail", "")

        # Correct password is also blocked while locked (check runs before verify)
        rc = s.post(f"{BASE_URL}/auth/login", json={"email": LOCK_EMAIL, "password": PASSWORD},
                    headers=headers, timeout=30)
        assert rc.status_code == 429, f"expected 429 while locked, got {rc.status_code}"

        # Age the row again -> successful login must work and clear the record
        mongo.login_attempts.update_one({"identifier": LOCK_IDENT},
                                        {"$set": {"last": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()}})
        ok = s.post(f"{BASE_URL}/auth/login", json={"email": LOCK_EMAIL, "password": PASSWORD},
                    headers=headers, timeout=30)
        assert ok.status_code == 200, f"login after window lapse failed: {ok.status_code} {ok.text}"
        assert ok.json()["email"] == LOCK_EMAIL.lower()
        assert mongo.login_attempts.find_one({"identifier": LOCK_IDENT}) is None, "record should be cleared on success"


# ---------- (b) Session invalidation on password reset ----------
class TestSessionInvalidationOnReset:
    def test_old_session_rejected_after_reset(self, mongo, reset_cleanup):
        mongo.users.delete_one({"email": RESET_EMAIL.lower()})
        old = requests.Session()
        _, r = register(RESET_EMAIL, PASSWORD, session=old)
        assert r.status_code == 200, r.text
        uid = r.json()["user_id"]

        # ensure explicit login session too (cookie from login path)
        lr = old.post(f"{BASE_URL}/auth/login", json={"email": RESET_EMAIL, "password": PASSWORD}, timeout=30)
        assert lr.status_code == 200, lr.text
        old_cookie = old.cookies.get("session_token")
        assert old_cookie

        me = old.get(f"{BASE_URL}/auth/me", timeout=30)
        assert me.status_code == 200, me.text
        assert me.json()["user_id"] == uid

        # ensure at least 1s gap so password_changed_at > token iat
        time.sleep(1.5)

        fp = requests.post(f"{BASE_URL}/auth/forgot-password", json={"email": RESET_EMAIL}, timeout=30)
        assert fp.status_code == 200, fp.text

        notes = old.get(f"{BASE_URL}/notifications", timeout=30)
        assert notes.status_code == 200, notes.text
        auth_notes = [n for n in notes.json() if n.get("kind") == "auth"]
        assert auth_notes, f"no auth notification found: {notes.text[:400]}"
        m = re.search(r"/reset-password\?token=([A-Za-z0-9_\-]+)", auth_notes[0]["message"])
        assert m, f"token not parsed from: {auth_notes[0]['message']}"
        token = m.group(1)

        rp = requests.post(f"{BASE_URL}/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}, timeout=30)
        assert rp.status_code == 200, rp.text
        assert mongo.users.find_one({"user_id": uid}).get("password_changed_at")

        # OLD session must now be rejected
        stale = requests.get(f"{BASE_URL}/auth/me", cookies={"session_token": old_cookie}, timeout=30)
        assert stale.status_code == 401, f"old session still valid: {stale.status_code} {stale.text}"
        assert stale.json().get("detail") == "Session invalidated. Please sign in again.", stale.text

        # Old password no longer works
        bad = requests.post(f"{BASE_URL}/auth/login", json={"email": RESET_EMAIL, "password": PASSWORD}, timeout=30)
        assert bad.status_code == 401, bad.text

        # New password works and its cookie is accepted
        fresh = requests.Session()
        nl = fresh.post(f"{BASE_URL}/auth/login", json={"email": RESET_EMAIL, "password": NEW_PASSWORD}, timeout=30)
        assert nl.status_code == 200, nl.text
        new_cookie = fresh.cookies.get("session_token")
        assert new_cookie and new_cookie != old_cookie
        me2 = fresh.get(f"{BASE_URL}/auth/me", timeout=30)
        assert me2.status_code == 200, f"new session rejected: {me2.status_code} {me2.text}"
        assert me2.json()["user_id"] == uid

        # reset token is single-use
        again = requests.post(f"{BASE_URL}/auth/reset-password", json={"token": token, "password": "An0therPass!"}, timeout=30)
        assert again.status_code == 400, again.text
