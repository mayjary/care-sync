"""Edge-case probes for validation gaps (report-only findings)."""
import os, requests
from dotenv import dotenv_values

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]).rstrip("/")
s = requests.Session()
s.post(f"{BASE}/api/auth/login", json={"email": "doctor2@careflow.demo", "password": "CareFlow123!"}).raise_for_status()
DOC = "demo_doctor_2"

# 1. duplicate weekday entries with overlapping periods across entries
payload = {"schedule": [
    {"weekday": 0, "periods": [{"start_time": "09:00", "end_time": "12:00"}], "duration": 30},
    {"weekday": 0, "periods": [{"start_time": "10:00", "end_time": "13:00"}], "duration": 30},
]}
r = s.put(f"{BASE}/api/availability/{DOC}", json=payload)
print("duplicate-weekday overlap PUT ->", r.status_code, r.text[:120])
print("GET after:", s.get(f"{BASE}/api/availability/{DOC}").json()["schedule"][0])

# 2. legacy POST /api/availability bypasses time validation
r2 = s.post(f"{BASE}/api/availability", json={"weekday": 0, "start_time": "99:99", "end_time": "aa:bb", "duration": 999})
print("legacy POST bad times ->", r2.status_code, r2.text[:200])
print("GET after legacy:", s.get(f"{BASE}/api/availability/{DOC}").json()["schedule"][0])

# restore
s.put(f"{BASE}/api/availability/{DOC}", json={"schedule": [{"weekday": wd, "periods": ([{"start_time": "09:00", "end_time": "13:00"}, {"start_time": "14:00", "end_time": "17:00"}] if wd < 5 else []), "duration": 30} for wd in range(7)]})
print("restored:", s.get(f"{BASE}/api/availability/{DOC}").json()["schedule"][0])
