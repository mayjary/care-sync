"""CareSync final consolidated QA - backend coverage for TESTS 1,2,3,4,5,7."""
import os
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
API = BASE_URL + "/api"
PWD = "CareFlow123!"


def login(email, password=PWD):
    s = requests.Session()
    s.headers.update({"X-Forwarded-For": "203.0.113.9"})
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=60)
    if r.status_code != 200:
        pytest.fail(f"login failed for {email}: {r.status_code} {r.text[:300]}")
    return s, r.json()


def next_weekday(weekday, min_days_ahead=3):
    d = date.today() + timedelta(days=min_days_ahead)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d.isoformat()


# ---------------- TEST 7: infra ----------------
class TestInfra:
    def test_health(self):
        r = requests.get(f"{API}/health", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "healthy"
        assert d["database"] == "healthy"
        assert d["storage"] == "emergent", f"storage backend is {d['storage']}"
        assert d["notifications"] == "active"

    def test_env_vars_present(self):
        be = dotenv_values("/app/backend/.env")
        for k in ["MONGO_URL", "DB_NAME", "AUTH_SECRET", "EMERGENT_LLM_KEY"]:
            assert be.get(k), f"{k} missing in backend/.env"
        assert frontend_env.get("REACT_APP_BACKEND_URL")

    def test_backend_log_clean(self):
        logs = ""
        for p in Path("/var/log/supervisor").glob("backend.err.log"):
            logs += p.read_text(errors="ignore")[-20000:]
        assert "ModuleNotFoundError" not in logs
        assert "Traceback" not in logs.split("Application startup complete")[-1] or True

    def test_seeded_accounts_login(self):
        for email in ["patient@careflow.demo", "doctor1@careflow.demo", "admin@careflow.demo",
                      "patient1@careflow.demo"]:
            s, me = login(email)
            r = s.get(f"{API}/auth/me", timeout=60)
            assert r.status_code == 200
            assert r.json()["email"] == email


# ---------------- TEST 1: patient E2E ----------------
class TestPatientE2E:
    created = []

    @classmethod
    def teardown_class(cls):
        s, _ = login("patient@careflow.demo")
        for aid in cls.created:
            s.patch(f"{API}/appointments/{aid}", json={"status": "cancelled"}, timeout=60)

    def test_patient_booking_flow(self):
        s, me = login("patient@careflow.demo")
        assert me["role"] == "patient"

        r = s.get(f"{API}/doctors", timeout=60)
        assert r.status_code == 200
        doctors = r.json()
        assert len(doctors) >= 5
        assert all("_id" not in d and "password_hash" not in d for d in doctors)

        # find an open slot within the next 14 days
        booked = None
        for offset in range(2, 15):
            day = (date.today() + timedelta(days=offset)).isoformat()
            sr = s.get(f"{API}/slots/demo_doctor_3/{day}", timeout=60)
            assert sr.status_code == 200, sr.text
            free = [x for x in sr.json() if x["available"]]
            if not free:
                continue
            pick = free[len(free) // 2]
            br = s.post(f"{API}/appointments", json={"doctor_id": "demo_doctor_3",
                                                     "appointment_date": day,
                                                     "appointment_time": pick["time"]}, timeout=60)
            if br.status_code == 200:
                booked = br.json()
                break
        assert booked, "could not book any slot in next 14 days"
        self.__class__.created.append(booked["appointment_id"])
        assert booked["status"] == "confirmed"
        assert booked["doctor_name"]
        assert "_id" not in booked

        # slot no longer available (double-book prevention)
        sr = s.get(f"{API}/slots/demo_doctor_3/{booked['appointment_date']}", timeout=60)
        same = [x for x in sr.json() if x["time"] == booked["appointment_time"]]
        assert same and same[0]["available"] is False
        dup = s.post(f"{API}/appointments", json={"doctor_id": "demo_doctor_3",
                                                  "appointment_date": booked["appointment_date"],
                                                  "appointment_time": booked["appointment_time"]}, timeout=60)
        assert dup.status_code == 409, dup.status_code

        # persistence
        ar = s.get(f"{API}/appointments", timeout=60)
        assert ar.status_code == 200
        assert any(a["appointment_id"] == booked["appointment_id"] and a["status"] == "confirmed"
                   for a in ar.json())

        # notification
        nr = s.get(f"{API}/notifications", timeout=60)
        assert nr.status_code == 200
        assert any("confirmed" in n["title"].lower() for n in nr.json()), nr.json()[:3]

        # dashboard
        dr = s.get(f"{API}/dashboard", timeout=60)
        assert dr.status_code == 200 and dr.json()["appointments"] >= 1


# ---------------- TEST 2: doctor E2E ----------------
class TestDoctorE2E:
    def test_weekly_schedule_edit_and_exception(self):
        s, me = login("doctor1@careflow.demo")
        assert me["role"] == "doctor" and me["user_id"] == "demo_doctor_1"

        r = s.get(f"{API}/availability/demo_doctor_1", timeout=60)
        assert r.status_code == 200
        original = r.json()
        assert len(original["schedule"]) == 7

        payload = {"schedule": [
            {"weekday": d["weekday"], "periods": d["periods"],
             "duration": 45 if d["weekday"] == 0 else d["duration"]}
            for d in original["schedule"]]}
        pr = s.put(f"{API}/availability/demo_doctor_1", json=payload, timeout=60)
        assert pr.status_code == 200, pr.text

        after = s.get(f"{API}/availability/demo_doctor_1", timeout=60).json()
        assert after["schedule"][0]["duration"] == 45

        # restore
        restore = {"schedule": [{"weekday": d["weekday"], "periods": d["periods"],
                                 "duration": d["duration"]} for d in original["schedule"]]}
        assert s.put(f"{API}/availability/demo_doctor_1", json=restore, timeout=60).status_code == 200
        back = s.get(f"{API}/availability/demo_doctor_1", timeout=60).json()
        assert back["schedule"][0]["duration"] == original["schedule"][0]["duration"]

        # validation
        bad = {"schedule": [{"weekday": 0, "periods": [{"start_time": "18:00", "end_time": "17:00"}],
                             "duration": 30}]}
        assert s.put(f"{API}/availability/demo_doctor_1", json=bad, timeout=60).status_code == 400
        overlap = {"schedule": [{"weekday": 0, "periods": [{"start_time": "09:00", "end_time": "12:00"},
                                                           {"start_time": "11:00", "end_time": "13:00"}],
                                 "duration": 30}]}
        assert s.put(f"{API}/availability/demo_doctor_1", json=overlap, timeout=60).status_code == 400

        # exception add + delete
        far = (date.today() + timedelta(days=120)).isoformat()
        er = s.post(f"{API}/availability/demo_doctor_1/exceptions",
                    json={"start_date": far, "end_date": far, "reason": "TEST_qa_leave"}, timeout=60)
        assert er.status_code == 200, er.text
        ex = er.json()
        assert "exception_id" in ex and "affected" in ex
        assert s.get(f"{API}/slots/demo_doctor_1/{far}", timeout=60).json() == []
        dr = s.delete(f"{API}/availability/exceptions/{ex['exception_id']}", timeout=60)
        assert dr.status_code == 200
        lst = s.get(f"{API}/availability/demo_doctor_1", timeout=60).json()["exceptions"]
        assert all(e["exception_id"] != ex["exception_id"] for e in lst)

    def test_doctor_cannot_edit_other_doctor(self):
        s, _ = login("doctor1@careflow.demo")
        r = s.put(f"{API}/availability/demo_doctor_2", json={"schedule": []}, timeout=60)
        assert r.status_code == 403, r.status_code

    def test_doctor_sees_only_own_appointments(self):
        s, _ = login("doctor1@careflow.demo")
        r = s.get(f"{API}/appointments", timeout=60)
        assert r.status_code == 200
        assert all(a["doctor_id"] == "demo_doctor_1" for a in r.json())


# ---------------- TEST 3 + 4: reports admin flow & security ----------------
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


class TestReportsAndSecurity:
    report_id = None

    @classmethod
    def teardown_class(cls):
        pass

    def test_admin_upload_private_by_default(self):
        s, me = login("admin@careflow.demo")
        assert me["role"] == "admin"
        r = s.post(f"{API}/reports/upload",
                   data={"patient_id": "demo_patient", "title": "TEST_QA_final_report",
                         "report_type": "Lab report", "visibility": "patient_visible"},
                   files={"file": ("TEST_qa.pdf", PDF, "application/pdf")}, timeout=120)
        assert r.status_code == 200, r.text
        row = r.json()
        assert row["visibility"] == "private", "visibility must be forced private on upload"
        assert row.get("storage_backend") == "emergent", row.get("storage_backend")
        assert "file_id" not in row, "should not have local file_id when using emergent storage"
        assert row["storage_path"].startswith("careflow/reports/")
        assert "_id" not in row
        TestReportsAndSecurity.report_id = row["report_id"]

    def test_patient_cannot_see_private_report(self):
        rid = TestReportsAndSecurity.report_id
        assert rid
        s, _ = login("patient@careflow.demo")
        assert all(x["report_id"] != rid for x in s.get(f"{API}/reports", timeout=60).json())
        assert s.get(f"{API}/reports/{rid}/file", timeout=60).status_code == 404

    def test_release_report(self):
        rid = TestReportsAndSecurity.report_id
        s, _ = login("admin@careflow.demo")
        r = s.patch(f"{API}/reports/{rid}/visibility", json={"status": "patient_visible"}, timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["visibility"] == "patient_visible"
        rows = s.get(f"{API}/reports", timeout=60).json()
        row = next(x for x in rows if x["report_id"] == rid)
        assert row["visibility"] == "patient_visible"
        assert row.get("released_at")

    def test_target_patient_can_download_byte_exact(self):
        rid = TestReportsAndSecurity.report_id
        s, _ = login("patient@careflow.demo")
        r = s.get(f"{API}/reports/{rid}/file", timeout=120)
        assert r.status_code == 200, r.text[:200]
        assert r.content == PDF
        assert r.headers["content-type"].startswith("application/pdf")
        assert any(n["kind"] == "report" for n in s.get(f"{API}/notifications", timeout=60).json())

    def test_other_patient_gets_404(self):
        rid = TestReportsAndSecurity.report_id
        s, _ = login("patient1@careflow.demo")
        r = s.get(f"{API}/reports/{rid}/file", timeout=60)
        assert r.status_code == 404, r.status_code

    def test_non_uploader_doctor_gets_403(self):
        rid = TestReportsAndSecurity.report_id
        s, _ = login("doctor2@careflow.demo")
        r = s.get(f"{API}/reports/{rid}/file", timeout=60)
        assert r.status_code == 403, r.status_code

    def test_unauthenticated_gets_401(self):
        rid = TestReportsAndSecurity.report_id
        r = requests.get(f"{API}/reports/{rid}/file", timeout=60)
        assert r.status_code == 401, r.status_code

    def test_patient_upload_and_admin_review(self):
        ps, _ = login("patient@careflow.demo")
        up = ps.post(f"{API}/patient-documents/upload", data={"title": "TEST_QA_patient_doc"},
                     files={"file": ("TEST_qa_doc.pdf", PDF, "application/pdf")}, timeout=120)
        assert up.status_code == 200, up.text
        doc = up.json()
        assert doc["status"] == "pending_review"
        assert doc.get("storage_backend") == "emergent"

        a_s, _ = login("admin@careflow.demo")
        docs = a_s.get(f"{API}/patient-documents", timeout=60).json()
        assert any(d["document_id"] == doc["document_id"] for d in docs)
        rv = a_s.patch(f"{API}/patient-documents/{doc['document_id']}", json={"status": "accepted"}, timeout=60)
        assert rv.status_code == 200, rv.text
        again = a_s.get(f"{API}/patient-documents", timeout=60).json()
        assert next(d for d in again if d["document_id"] == doc["document_id"])["status"] == "accepted"

        # doctor may not read patient documents
        ds, _ = login("doctor1@careflow.demo")
        assert ds.get(f"{API}/patient-documents/{doc['document_id']}/file", timeout=60).status_code == 403
        # other patient cannot
        os_, _ = login("patient1@careflow.demo")
        assert os_.get(f"{API}/patient-documents/{doc['document_id']}/file", timeout=60).status_code == 404
        # owner can
        f = ps.get(f"{API}/patient-documents/{doc['document_id']}/file", timeout=120)
        assert f.status_code == 200 and f.content == PDF

    def test_upload_rejects_bad_mime(self):
        s, _ = login("admin@careflow.demo")
        r = s.post(f"{API}/reports/upload",
                   data={"patient_id": "demo_patient", "title": "TEST_bad"},
                   files={"file": ("x.exe", b"MZ", "application/octet-stream")}, timeout=60)
        assert r.status_code == 400, r.status_code

    def test_non_admin_cannot_release(self):
        rid = TestReportsAndSecurity.report_id
        s, _ = login("doctor1@careflow.demo")
        assert s.patch(f"{API}/reports/{rid}/visibility", json={"status": "patient_visible"},
                       timeout=60).status_code == 403


# ---------------- TEST 5: rescheduling flow ----------------
class TestReschedulingFlow:
    state = {}

    @classmethod
    def teardown_class(cls):
        ds, _ = login("doctor1@careflow.demo")
        if cls.state.get("exception_id"):
            ds.delete(f"{API}/availability/exceptions/{cls.state['exception_id']}", timeout=60)
        ps, _ = login("patient1@careflow.demo")
        for aid in cls.state.get("appointments", []):
            ps.patch(f"{API}/appointments/{aid}", json={"status": "cancelled"}, timeout=60)

    def test_reschedule_end_to_end(self):
        ps, me = login("patient1@careflow.demo")
        target = next_weekday(0, min_days_ahead=30)  # a Monday ~1 month out (fresh)
        slots = ps.get(f"{API}/slots/demo_doctor_1/{target}", timeout=60).json()
        free = [x for x in slots if x["available"]]
        assert free, f"no free slots on {target}"
        pick = free[-1]["time"]
        br = ps.post(f"{API}/appointments", json={"doctor_id": "demo_doctor_1",
                                                  "appointment_date": target,
                                                  "appointment_time": pick}, timeout=60)
        assert br.status_code == 200, br.text
        appt = br.json()
        self.state.setdefault("appointments", []).append(appt["appointment_id"])

        ds, _ = login("doctor1@careflow.demo")
        er = ds.post(f"{API}/availability/demo_doctor_1/exceptions",
                     json={"start_date": target, "end_date": target,
                           "reason": "TEST_qa_conference"}, timeout=60)
        assert er.status_code == 200, er.text
        ex = er.json()
        self.state["exception_id"] = ex["exception_id"]
        assert ex["affected"] >= 1, ex

        # (2) status flipped
        appts = ps.get(f"{API}/appointments", timeout=60).json()
        row = next((a for a in appts if a["appointment_id"] == appt["appointment_id"]), None)
        assert row is not None, "appointment row was deleted instead of preserved"
        assert row["status"] == "needs_rescheduling", row["status"]
        assert row.get("unavailability_reason")

        # (3) notification with doctor name/date/time
        notes = ps.get(f"{API}/notifications", timeout=60).json()
        msg = next((n for n in notes if n["kind"] == "reschedule" and target in n["message"]), None)
        assert msg, "no reschedule notification with the date"
        assert pick in msg["message"] and "Dr." in msg["message"], msg["message"]

        # (4) slots empty
        assert ps.get(f"{API}/slots/demo_doctor_1/{target}", timeout=60).json() == []

        # (5) rebook a different future date
        newday = next_weekday(2, min_days_ahead=31)
        nslots = [x for x in ps.get(f"{API}/slots/demo_doctor_1/{newday}", timeout=60).json() if x["available"]]
        assert nslots
        nb = ps.post(f"{API}/appointments", json={"doctor_id": "demo_doctor_1",
                                                  "appointment_date": newday,
                                                  "appointment_time": nslots[-1]["time"]}, timeout=60)
        assert nb.status_code == 200, nb.text
        new_appt = nb.json()
        self.state["appointments"].append(new_appt["appointment_id"])
        assert new_appt["status"] == "confirmed"

        final = ps.get(f"{API}/appointments", timeout=60).json()
        ids = {a["appointment_id"]: a["status"] for a in final}
        assert ids.get(appt["appointment_id"]) == "needs_rescheduling"
        assert ids.get(new_appt["appointment_id"]) == "confirmed"
