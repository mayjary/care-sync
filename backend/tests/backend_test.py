import os
import requests
import pytest

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


def login(email, password="CareFlow123!"):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    return s, r


def test_seeded_roles_and_me():
    for email, role in [("patient@careflow.demo", "patient"), ("doctor1@careflow.demo", "doctor"), ("admin@careflow.demo", "admin")]:
        s, r = login(email)
        assert r.status_code == 200 and r.json()["role"] == role
        me = s.get(f"{BASE_URL}/api/auth/me")
        assert me.status_code == 200 and me.json()["email"] == email
        assert "password_hash" not in me.json()


def test_invalid_password_and_protected_route():
    s, r = login("patient@careflow.demo", "wrong-password")
    assert r.status_code == 401
    assert requests.get(f"{BASE_URL}/api/dashboard").status_code == 401


def test_logout_invalidates_cookie():
    s, r = login("patient@careflow.demo")
    assert r.status_code == 200
    assert s.post(f"{BASE_URL}/api/auth/logout").status_code == 200
    assert s.get(f"{BASE_URL}/api/auth/me").status_code == 401


def test_doctors_and_slots():
    r = requests.get(f"{BASE_URL}/api/doctors")
    assert r.status_code == 200 and len(r.json()) >= 5
    doctor_id = r.json()[0]["user_id"]
    bad = requests.get(f"{BASE_URL}/api/slots/{doctor_id}/not-a-date")
    assert bad.status_code == 400


def test_role_permissions_and_patient_views():
    patient, r = login("patient@careflow.demo")
    assert r.status_code == 200
    for endpoint in ["dashboard", "appointments", "notifications", "records", "reports"]:
        response = patient.get(f"{BASE_URL}/api/{endpoint}")
        assert response.status_code == 200 and isinstance(response.json(), (dict, list))
    assert patient.post(f"{BASE_URL}/api/records/demo_patient_1/notes", json={"note": "x"}).status_code == 403


def test_legal_and_health():
    assert requests.get(f"{BASE_URL}/api/health").status_code == 200
    for path in ["/", "/privacy", "/terms", "/disclaimer"]:
        assert requests.get(f"{BASE_URL}{path}").status_code in [200, 404]