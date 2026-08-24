"""Iteration 7 UI setup: create a needs_rescheduling appointment for patient@careflow.demo. Modes: setup | cleanup"""
import os, sys
from datetime import date, timedelta
import requests
from dotenv import dotenv_values

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]).rstrip("/")
DOC = "demo_doctor_4"


def login(email):
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": email, "password": "CareFlow123!"})
    r.raise_for_status()
    return s


mode = sys.argv[1] if len(sys.argv) > 1 else "setup"
pat = login("patient@careflow.demo")
doc = login("doctor4@careflow.demo")

if mode == "setup":
    d = date.today() + timedelta(days=25)
    while d.weekday() > 4:
        d += timedelta(days=1)
    slots = [x["time"] for x in requests.get(f"{BASE}/api/slots/{DOC}/{d.isoformat()}").json() if x["available"]]
    print("date", d, "slots", slots[:3])
    b = pat.post(f"{BASE}/api/appointments", json={"doctor_id": DOC, "appointment_date": d.isoformat(), "appointment_time": slots[0], "reason": "UITEST it7"})
    print("booked", b.status_code, b.json().get("appointment_id"))
    ex = doc.post(f"{BASE}/api/availability/{DOC}/exceptions", json={"start_date": d.isoformat(), "end_date": d.isoformat(), "reason": "UITEST it7 block"})
    print("blocked", ex.status_code, ex.json())
    appts = pat.get(f"{BASE}/api/appointments").json()
    print("statuses", [(a["appointment_id"], a["status"]) for a in appts])

if mode == "cleanup":
    g = doc.get(f"{BASE}/api/availability/{DOC}").json()
    for e in g["exceptions"]:
        if "UITEST" in (e.get("reason") or ""):
            print("delete ex", e["exception_id"], doc.delete(f"{BASE}/api/availability/exceptions/{e['exception_id']}").status_code)
    for a in pat.get(f"{BASE}/api/appointments").json():
        if a["status"] == "needs_rescheduling":
            print("cancel", a["appointment_id"], pat.patch(f"{BASE}/api/appointments/{a['appointment_id']}", json={"status": "cancelled"}).status_code)
