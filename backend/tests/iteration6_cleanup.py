"""Cleanup of iteration-6 UI test artifacts."""
import os, requests
from dotenv import dotenv_values

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]).rstrip("/")

def login(email):
    s = requests.Session()
    s.post(f"{BASE}/api/auth/login", json={"email": email, "password": "CareFlow123!"}).raise_for_status()
    return s

adm = login("admin@careflow.demo")
# remove UITEST exception blocks
for i in range(1, 6):
    g = adm.get(f"{BASE}/api/availability/demo_doctor_{i}").json()
    for e in g["exceptions"]:
        if "UITEST" in (e.get("reason") or ""):
            print("del exception", e["exception_id"], e["reason"], adm.delete(f"{BASE}/api/availability/exceptions/{e['exception_id']}").status_code)

p2 = login("patient2@careflow.demo")
for a in p2.get(f"{BASE}/api/appointments").json():
    if a["appointment_date"] >= "2026-09-01" and a["status"] in ("confirmed", "needs_rescheduling"):
        r = p2.patch(f"{BASE}/api/appointments/{a['appointment_id']}", json={"status": "cancelled"})
        print("cancelled", a["appointment_id"], a["appointment_date"], a["appointment_time"], r.status_code)

print("final doctor1:", adm.get(f"{BASE}/api/availability/demo_doctor_1").json())
