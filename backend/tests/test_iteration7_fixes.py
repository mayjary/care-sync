"""Iteration 7 regression: fixes to iteration-6 findings.

FIX 1 - POST /api/availability (legacy) validates times + duration and preserves computed doctor_id.
FIX 2 - PUT /api/availability/{doctor_id} groups periods by weekday for overlap detection.
FIX 6 - GET /api/patient-documents/{id}/file access control (own 200, other patient 404, doctor 403).
FIX 7 - appointment status needs_rescheduling reachable (data for Rebook link).
"""
import os
import io
import uuid

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing from env and /app/frontend/.env")
BASE_URL = base_url.rstrip("/")
PWD = "CareFlow123!"
RUN = uuid.uuid4().hex[:6]

DOCTOR3 = "demo_doctor_3"


def client_for(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PWD})
    if r.status_code != 200:
        pytest.fail(f"login failed for {email}: {r.status_code} {r.text[:200]}")
    return s


def default_schedule():
    return {"schedule": [{"weekday": wd,
                          "periods": ([{"start_time": "09:00", "end_time": "13:00"},
                                       {"start_time": "14:00", "end_time": "17:00"}] if wd < 5 else []),
                          "duration": 30} for wd in range(7)]}


@pytest.fixture(scope="module")
def admin():
    return client_for("admin@careflow.demo")


@pytest.fixture(scope="module")
def doctor3():
    return client_for("doctor3@careflow.demo")


@pytest.fixture(scope="module")
def patient():
    return client_for("patient@careflow.demo")


@pytest.fixture(scope="module")
def patient2():
    return client_for("patient2@careflow.demo")


# ---------------------------------------------------------------- FIX 1
class TestFix1LegacyPost:
    """POST /api/availability validation + doctor_id preservation."""

    @pytest.fixture(scope="class", autouse=True)
    def restore(self, request):
        yield
        adm = client_for("admin@careflow.demo")
        r = adm.put(f"{BASE_URL}/api/availability/{DOCTOR3}", json=default_schedule())
        assert r.status_code == 200

    @pytest.mark.parametrize("payload,label", [
        ({"weekday": 2, "start_time": "99:99", "end_time": "12:00", "duration": 30}, "bad start"),
        ({"weekday": 2, "start_time": "09:00", "end_time": "aa:bb", "duration": 30}, "bad end"),
        ({"weekday": 2, "start_time": "9:0", "end_time": "12:00", "duration": 30}, "unpadded"),
        ({"weekday": 2, "start_time": "25:00", "end_time": "26:00", "duration": 30}, "hour 25"),
        ({"weekday": 2, "start_time": "12:00", "end_time": "09:00", "duration": 30}, "start>end"),
        ({"weekday": 2, "start_time": "12:00", "end_time": "12:00", "duration": 30}, "equal"),
    ])
    def test_invalid_times_rejected(self, doctor3, payload, label):
        r = doctor3.post(f"{BASE_URL}/api/availability", json=payload)
        assert r.status_code == 400, f"{label}: expected 400, got {r.status_code} {r.text[:200]}"
        assert "detail" in r.json()

    @pytest.mark.parametrize("duration", [4, 0, -10, 241, 999])
    def test_invalid_duration_rejected(self, doctor3, duration):
        r = doctor3.post(f"{BASE_URL}/api/availability",
                         json={"weekday": 2, "start_time": "09:00", "end_time": "12:00", "duration": duration})
        assert r.status_code == 400, f"duration {duration}: got {r.status_code} {r.text[:200]}"
        assert "5 and 240" in r.json()["detail"]

    def test_valid_post_preserves_doctor_id(self, doctor3, admin):
        # start clean for weekday 6 (Sunday) so the row is easy to identify
        assert admin.put(f"{BASE_URL}/api/availability/{DOCTOR3}", json=default_schedule()).status_code == 200
        r = doctor3.post(f"{BASE_URL}/api/availability",
                         json={"weekday": 6, "start_time": "18:00", "end_time": "20:00", "duration": 20})
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["doctor_id"] == DOCTOR3, f"computed doctor_id lost: {body}"
        assert "_id" not in body
        assert body["availability_id"].startswith("av_")
        assert body["weekday"] == 6 and body["start_time"] == "18:00" and body["duration"] == 20

        # persistence: the row must show up on the doctor's own weekly schedule
        g = doctor3.get(f"{BASE_URL}/api/availability/{DOCTOR3}")
        assert g.status_code == 200
        sunday = [d for d in g.json()["schedule"] if d["weekday"] == 6][0]
        assert {"start_time": "18:00", "end_time": "20:00"} in sunday["periods"]
        assert sunday["duration"] == 20

        # and slots must be generated on the next Sunday
        from datetime import date, timedelta
        d = date.today() + timedelta(days=1)
        while d.weekday() != 6:
            d += timedelta(days=1)
        slots = doctor3.get(f"{BASE_URL}/api/slots/{DOCTOR3}/{d.isoformat()}")
        assert slots.status_code == 200
        assert "18:00" in [x["time"] for x in slots.json()], slots.json()

    def test_admin_post_requires_valid_doctor(self, admin):
        r = admin.post(f"{BASE_URL}/api/availability",
                       json={"doctor_id": "no_such_doctor", "weekday": 1,
                             "start_time": "09:00", "end_time": "10:00", "duration": 30})
        assert r.status_code == 400
        r2 = admin.post(f"{BASE_URL}/api/availability",
                        json={"weekday": 1, "start_time": "09:00", "end_time": "10:00", "duration": 30})
        assert r2.status_code == 400

    def test_admin_post_preserves_target_doctor_id(self, admin):
        r = admin.post(f"{BASE_URL}/api/availability",
                       json={"doctor_id": DOCTOR3, "weekday": 5,
                             "start_time": "10:00", "end_time": "11:00", "duration": 15})
        assert r.status_code == 200, r.text[:300]
        assert r.json()["doctor_id"] == DOCTOR3

    def test_patient_cannot_post(self, patient):
        r = patient.post(f"{BASE_URL}/api/availability",
                         json={"weekday": 1, "start_time": "09:00", "end_time": "10:00", "duration": 30})
        assert r.status_code == 403


# ---------------------------------------------------------------- FIX 2
class TestFix2WeekdayGroupedOverlap:
    """PUT /api/availability/{doctor_id} groups periods by weekday before overlap check."""

    @pytest.fixture(scope="class", autouse=True)
    def restore(self):
        yield
        adm = client_for("admin@careflow.demo")
        assert adm.put(f"{BASE_URL}/api/availability/{DOCTOR3}", json=default_schedule()).status_code == 200

    def _put(self, cli, schedule):
        return cli.put(f"{BASE_URL}/api/availability/{DOCTOR3}", json={"schedule": schedule})

    def test_duplicate_weekday_overlapping_rejected(self, admin):
        r = self._put(admin, [
            {"weekday": 0, "periods": [{"start_time": "09:00", "end_time": "12:00"}], "duration": 30},
            {"weekday": 0, "periods": [{"start_time": "10:00", "end_time": "13:00"}], "duration": 30},
        ])
        assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text[:200]}"
        assert "overlap" in r.json()["detail"].lower()

    def test_duplicate_weekday_touching_boundary_ok(self, admin):
        # 09:00-12:00 then 12:00-15:00 on the same weekday: adjacent, not overlapping
        r = self._put(admin, [
            {"weekday": 0, "periods": [{"start_time": "09:00", "end_time": "12:00"}], "duration": 30},
            {"weekday": 0, "periods": [{"start_time": "12:00", "end_time": "15:00"}], "duration": 30},
        ])
        assert r.status_code == 200, r.text[:300]
        g = admin.get(f"{BASE_URL}/api/availability/{DOCTOR3}").json()
        monday = [d for d in g["schedule"] if d["weekday"] == 0][0]
        assert sorted([p["start_time"] for p in monday["periods"]]) == ["09:00", "12:00"]

    def test_duplicate_weekday_non_overlapping_ok(self, admin):
        r = self._put(admin, [
            {"weekday": 1, "periods": [{"start_time": "09:00", "end_time": "11:00"}], "duration": 30},
            {"weekday": 1, "periods": [{"start_time": "14:00", "end_time": "16:00"}], "duration": 30},
            {"weekday": 2, "periods": [{"start_time": "09:00", "end_time": "11:00"}], "duration": 30},
        ])
        assert r.status_code == 200, r.text[:300]
        g = admin.get(f"{BASE_URL}/api/availability/{DOCTOR3}").json()
        tue = [d for d in g["schedule"] if d["weekday"] == 1][0]
        assert sorted([p["start_time"] for p in tue["periods"]]) == ["09:00", "14:00"]

    def test_same_weekday_contained_period_rejected(self, admin):
        r = self._put(admin, [
            {"weekday": 3, "periods": [{"start_time": "09:00", "end_time": "17:00"}], "duration": 30},
            {"weekday": 3, "periods": [{"start_time": "10:00", "end_time": "11:00"}], "duration": 30},
        ])
        assert r.status_code == 400
        assert "overlap" in r.json()["detail"].lower()

    def test_same_entry_overlap_still_rejected(self, admin):
        r = self._put(admin, [
            {"weekday": 4, "periods": [{"start_time": "09:00", "end_time": "12:00"},
                                       {"start_time": "11:00", "end_time": "13:00"}], "duration": 30},
        ])
        assert r.status_code == 400

    def test_different_weekdays_same_hours_ok(self, admin):
        r = self._put(admin, [{"weekday": wd, "periods": [{"start_time": "09:00", "end_time": "12:00"}],
                               "duration": 30} for wd in range(7)])
        assert r.status_code == 200, r.text[:300]

    def test_invalid_time_in_duplicate_entry_rejected(self, admin):
        r = self._put(admin, [
            {"weekday": 0, "periods": [{"start_time": "09:00", "end_time": "12:00"}], "duration": 30},
            {"weekday": 0, "periods": [{"start_time": "9:0", "end_time": "13:00"}], "duration": 30},
        ])
        assert r.status_code == 400


# ---------------------------------------------------------------- FIX 6
class TestFix6DocumentFileAccess:
    """GET /api/patient-documents/{id}/file access control."""

    @pytest.fixture(scope="class")
    def uploaded(self, patient):
        content = b"\x89PNG\r\n\x1a\n" + b"careflow-iteration7-file-bytes"
        r = patient.post(f"{BASE_URL}/api/patient-documents/upload",
                         data={"title": f"TEST_it7_{RUN}", "notes": "iteration 7"},
                         files={"file": (f"TEST_it7_{RUN}.png", io.BytesIO(content), "image/png")})
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert "_id" not in body
        assert body["status"] == "pending_review"
        return body["document_id"], content

    def test_owner_can_view(self, patient, uploaded):
        doc_id, content = uploaded
        r = patient.get(f"{BASE_URL}/api/patient-documents/{doc_id}/file")
        assert r.status_code == 200, r.text[:200]
        assert r.content == content

    def test_other_patient_gets_404(self, patient2, uploaded):
        doc_id, _ = uploaded
        r = patient2.get(f"{BASE_URL}/api/patient-documents/{doc_id}/file")
        assert r.status_code == 404, r.status_code

    def test_doctor_gets_403(self, doctor3, uploaded):
        doc_id, _ = uploaded
        r = doctor3.get(f"{BASE_URL}/api/patient-documents/{doc_id}/file")
        assert r.status_code == 403, r.status_code

    def test_admin_can_view(self, admin, uploaded):
        doc_id, content = uploaded
        r = admin.get(f"{BASE_URL}/api/patient-documents/{doc_id}/file")
        assert r.status_code == 200
        assert r.content == content

    def test_unauthenticated_gets_401(self, uploaded):
        doc_id, _ = uploaded
        r = requests.get(f"{BASE_URL}/api/patient-documents/{doc_id}/file")
        assert r.status_code == 401, r.status_code

    def test_unknown_document_404(self, patient):
        r = patient.get(f"{BASE_URL}/api/patient-documents/doc_does_not_exist/file")
        assert r.status_code == 404

    def test_document_appears_in_own_list(self, patient, uploaded):
        doc_id, _ = uploaded
        r = patient.get(f"{BASE_URL}/api/patient-documents")
        assert r.status_code == 200
        docs = r.json()
        assert any(d["document_id"] == doc_id for d in docs)
        assert all("_id" not in d for d in docs)

    def test_upload_requires_title_and_file(self, patient):
        r = patient.post(f"{BASE_URL}/api/patient-documents/upload",
                         data={"title": ""},
                         files={"file": ("empty.png", io.BytesIO(b"x"), "image/png")})
        assert r.status_code in (400, 422), f"{r.status_code} {r.text[:200]}"


# ---------------------------------------------------------------- FIX 7 data
class TestFix7NeedsReschedulingData:
    """An unavailability block flags appointments as needs_rescheduling (drives the Rebook link)."""

    def test_block_flags_appointment_and_notifies(self, patient, doctor3, admin):
        from datetime import date, timedelta
        # ensure default schedule
        assert admin.put(f"{BASE_URL}/api/availability/{DOCTOR3}", json=default_schedule()).status_code == 200
        d = date.today() + timedelta(days=21)
        while d.weekday() > 4:
            d += timedelta(days=1)
        slots = [x["time"] for x in doctor3.get(f"{BASE_URL}/api/slots/{DOCTOR3}/{d.isoformat()}").json() if x["available"]]
        assert slots, "no slots to book"
        book = patient.post(f"{BASE_URL}/api/appointments",
                            json={"doctor_id": DOCTOR3, "appointment_date": d.isoformat(),
                                  "appointment_time": slots[0], "reason": "TEST_it7 rebook flow"})
        assert book.status_code == 200, book.text[:300]
        appt_id = book.json()["appointment_id"]
        try:
            ex = doctor3.post(f"{BASE_URL}/api/availability/{DOCTOR3}/exceptions",
                              json={"start_date": d.isoformat(), "end_date": d.isoformat(),
                                    "reason": "TEST_it7 block"})
            assert ex.status_code == 200, ex.text[:300]
            assert ex.json()["affected"] >= 1
            ex_id = ex.json()["exception_id"]

            appts = patient.get(f"{BASE_URL}/api/appointments").json()
            mine = [a for a in appts if a["appointment_id"] == appt_id][0]
            assert mine["status"] == "needs_rescheduling", mine
            assert mine["appointment_date"] == d.isoformat()

            # blocked date has no slots
            assert doctor3.get(f"{BASE_URL}/api/slots/{DOCTOR3}/{d.isoformat()}").json() == []

            notes = patient.get(f"{BASE_URL}/api/notifications").json()
            assert any(d.isoformat() in (n.get("message") or "") for n in notes), "no reschedule notification"

            # DELETE frees the date again
            assert doctor3.delete(f"{BASE_URL}/api/availability/exceptions/{ex_id}").status_code == 200
            assert slots[0] in [x["time"] for x in doctor3.get(f"{BASE_URL}/api/slots/{DOCTOR3}/{d.isoformat()}").json()]
        finally:
            patient.patch(f"{BASE_URL}/api/appointments/{appt_id}", json={"status": "cancelled"})
