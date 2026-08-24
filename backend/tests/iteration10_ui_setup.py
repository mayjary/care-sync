"""Prepare a throwaway user + reset token for iteration 10 UI testing."""
import json
import re
import uuid

import requests
from dotenv import dotenv_values

BASE = dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"
email = f"test_ui_{uuid.uuid4().hex[:8]}@careflow.demo"
pw = "StrongPass1!"
s = requests.Session()
s.headers.update({"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.9"})
r = s.post(f"{API}/auth/register", json={"name": "TEST UI", "email": email, "password": pw, "role": "patient"})
assert r.status_code == 200, r.text
uid = r.json()["user_id"]
assert s.post(f"{API}/auth/forgot-password", json={"email": email}).status_code == 200
notes = s.get(f"{API}/notifications").json()
tok = None
for n in notes:
    if n.get("kind") == "auth":
        m = re.search(r"/reset-password\?token=([A-Za-z0-9_\-]+)", n.get("message", ""))
        if m:
            tok = m.group(1)
            break
assert tok, notes
print(json.dumps({"email": email, "old_password": pw, "token": tok, "user_id": uid}))
