"""Diagnostic: check whether the 09:00/10:00 next-Monday slots used by legacy tests are stale-booked."""
import os
from datetime import date, timedelta
import requests
from dotenv import dotenv_values

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]).rstrip("/")
doctors = requests.get(f"{BASE}/api/doctors").json()
d0 = doctors[0]
print("doctors[0]:", d0["user_id"], d0["name"])
target = date.today() + timedelta(days=1)
while target.weekday() != 0:
    target += timedelta(days=1)
print("target Monday:", target.isoformat())
slots = requests.get(f"{BASE}/api/slots/{d0['user_id']}/{target.isoformat()}").json()
print("slots:", slots[:8])
adm = requests.Session()
adm.post(f"{BASE}/api/auth/login", json={"email": "admin@careflow.demo", "password": "CareFlow123!"})
appts = [a for a in adm.get(f"{BASE}/api/appointments").json()
         if a["doctor_id"] == d0["user_id"] and a["appointment_date"] == target.isoformat()]
for a in appts:
    print("existing:", a["appointment_id"], a["appointment_time"], a["status"], a["patient_name"])
sched = adm.get(f"{BASE}/api/availability/{d0['user_id']}").json()
print("weekly mon:", sched["schedule"][0])
print("exceptions:", sched["exceptions"])
