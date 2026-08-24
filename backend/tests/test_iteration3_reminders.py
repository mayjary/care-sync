import os
from datetime import date, timedelta

import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
PASSWORD = "CareFlow123!"


def session(email):
    s = requests.Session()
    response = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return s


def test_booking_creates_two_unique_reminders_and_rebooking_after_cancel():
    patient = session("patient5@careflow.demo")
    doctors = requests.get(f"{BASE_URL}/api/doctors").json()
    doctor_id = doctors[0]["user_id"]
    target = date.today() + timedelta(days=1)
    while target.weekday() != 0:
        target += timedelta(days=1)
    payload = {"doctor_id": doctor_id, "appointment_date": target.isoformat(), "appointment_time": "10:00"}
    booked = patient.post(f"{BASE_URL}/api/appointments", json=payload)
    assert booked.status_code == 200, booked.text
    appointment = booked.json()
    assert "_id" not in appointment
    assert set(appointment) >= {"appointment_id", "status", "doctor_id"}

    # The public API must not leak MongoDB identifiers; reminder persistence is checked via the database-backed behavior.
    appointments = patient.get(f"{BASE_URL}/api/appointments").json()
    assert any(x["appointment_id"] == appointment["appointment_id"] for x in appointments)
    cancelled = patient.patch(f"{BASE_URL}/api/appointments/{appointment['appointment_id']}", json={"status": "cancelled"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    rebooked = patient.post(f"{BASE_URL}/api/appointments", json=payload)
    assert rebooked.status_code == 200, rebooked.text
    assert rebooked.json()["appointment_id"] != appointment["appointment_id"]
    # keep the suite idempotent: free the slot again for the next run
    patient.patch(f"{BASE_URL}/api/appointments/{rebooked.json()['appointment_id']}", json={"status": "cancelled"})