import os, requests, datetime
from dotenv import dotenv_values
API = (dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]).rstrip("/") + "/api"
s = requests.Session(); s.headers.update({"X-Forwarded-For": "203.0.113.9"})
s.post(f"{API}/auth/login", json={"email": "patient@careflow.demo", "password": "CareFlow123!"}, timeout=60)
today = datetime.date.today().isoformat()
print("now UTC:", datetime.datetime.now(datetime.timezone.utc).isoformat())
slots = s.get(f"{API}/slots/demo_doctor_4/{today}", timeout=60).json()
print("today's slots:", slots)
r = s.post(f"{API}/appointments", json={"doctor_id": "demo_doctor_4", "appointment_date": today, "appointment_time": "09:00"}, timeout=60)
print("book past slot 09:00 today ->", r.status_code, r.text[:200])
past = datetime.date.today() - datetime.timedelta(days=3)
pr = s.post(f"{API}/appointments", json={"doctor_id": "demo_doctor_4", "appointment_date": past.isoformat(), "appointment_time": "10:00"}, timeout=60)
print("book date 3 days ago ->", pr.status_code, pr.text[:200])

# cleanup: cancel anything created today by this probe and the UI test
appts = s.get(f"{API}/appointments", timeout=60).json()
for a in appts:
    if a["status"] == "confirmed" and a["appointment_date"] <= today:
        c = s.patch(f"{API}/appointments/{a['appointment_id']}", json={"status": "cancelled"}, timeout=60)
        print("cancelled", a["appointment_id"], a["appointment_date"], a["appointment_time"], a["doctor_name"], c.status_code)
