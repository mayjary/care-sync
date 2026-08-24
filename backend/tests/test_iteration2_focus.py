import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
PASSWORD = "CareFlow123!"


def session(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return s


def test_admin_targets_selected_doctor_and_report_permissions():
    admin = session("admin@careflow.demo")
    doctors = requests.get(f"{BASE_URL}/api/doctors").json()
    doctor_id = doctors[1]["user_id"]
    r = admin.post(f"{BASE_URL}/api/availability", json={"doctor_id": doctor_id, "weekday": 6, "start_time": "10:00", "end_time": "11:00", "duration": 30})
    assert r.status_code == 200 and r.json()["doctor_id"] == doctor_id

    content = b"%PDF-1.4 CareFlow test"
    upload = admin.post(f"{BASE_URL}/api/reports/upload", data={"patient_id": "demo_patient", "title": "TEST private report", "visibility": "private"}, files={"file": ("test.pdf", content, "application/pdf")})
    assert upload.status_code == 200 and "_id" not in upload.json()
    report_id = upload.json()["report_id"]
    patient = session("patient@careflow.demo")
    assert all(x["report_id"] != report_id for x in patient.get(f"{BASE_URL}/api/reports").json())
    assert patient.get(f"{BASE_URL}/api/reports/{report_id}/file").status_code == 404


def test_concurrent_same_slot_is_single_success():
    doctors = requests.get(f"{BASE_URL}/api/doctors").json()
    doctor_id = doctors[0]["user_id"]
    # Find a future Monday matching the seeded doctor's weekday rule.
    target = date.today() + timedelta(days=1)
    while target.weekday() != 0:
        target += timedelta(days=1)
    payload = {"doctor_id": doctor_id, "appointment_date": target.isoformat(), "appointment_time": "09:00"}
    patients = [session("patient1@careflow.demo"), session("patient2@careflow.demo")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda s: s.post(f"{BASE_URL}/api/appointments", json=payload), patients))
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(r for r in responses if r.status_code == 200).json()
    assert "_id" not in winner
    # keep the suite idempotent: free the slot again for the next run
    for s in patients:
        s.patch(f"{BASE_URL}/api/appointments/{winner['appointment_id']}", json={"status": "cancelled"})