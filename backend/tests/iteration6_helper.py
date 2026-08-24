"""Cleanup + integration setup for iteration 6 UI checks."""
import os, sys, requests
from dotenv import dotenv_values
from datetime import date, timedelta

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]).rstrip("/")
s = requests.Session()
s.post(f"{BASE}/api/auth/login", json={"email": "admin@careflow.demo", "password": "CareFlow123!"}).raise_for_status()

mode = sys.argv[1] if len(sys.argv) > 1 else "status"
DOC = "demo_doctor_1"

def default_schedule():
    return {"schedule": [{"weekday": wd, "periods": ([{"start_time": "09:00", "end_time": "13:00"}, {"start_time": "14:00", "end_time": "17:00"}] if wd < 5 else []), "duration": 30} for wd in range(7)]}

if mode == "status":
    for d in range(1, 6):
        g = s.get(f"{BASE}/api/availability/demo_doctor_{d}").json()
        print(f"demo_doctor_{d}: mon={g['schedule'][0]['periods']} dur={g['schedule'][0]['duration']} thu={g['schedule'][3]['periods']} exceptions={[(e['exception_id'], e['start_date'], e['end_date'], e['reason']) for e in g['exceptions']]}")

if mode == "restore":
    for d in range(1, 6):
        r = s.put(f"{BASE}/api/availability/demo_doctor_{d}", json=default_schedule())
        print("restored", d, r.status_code)
    # remove leftover UI-test exceptions
    for d in range(1, 6):
        g = s.get(f"{BASE}/api/availability/demo_doctor_{d}").json()
        for e in g["exceptions"]:
            if any(k in (e.get("reason") or "") for k in ["UITEST", "TEST_", "Conference", "conference"]):
                print("deleting", e["exception_id"], e["reason"], s.delete(f"{BASE}/api/availability/exceptions/{e['exception_id']}").status_code)

if mode == "block":
    d = (date.today() + timedelta(days=8))
    while d.weekday() > 4:
        d += timedelta(days=1)
    r = s.post(f"{BASE}/api/availability/{DOC}/exceptions", json={"start_date": d.isoformat(), "end_date": d.isoformat(), "reason": "UITEST block"})
    print("BLOCKED_DATE", d.isoformat(), r.status_code, r.json())
