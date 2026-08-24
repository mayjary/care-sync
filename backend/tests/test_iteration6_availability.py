"""Iteration 6 regression: doctor availability editor, unavailability blocks, patient uploads."""
import os
import io
import uuid
from datetime import date, timedelta

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
PWD = "CareFlow123!"
RUN = uuid.uuid4().hex[:6]

DOCTOR1 = "demo_doctor_1"
DOCTOR2 = "demo_doctor_2"
DOCTOR3 = "demo_doctor_3"
DOCTOR4 = "demo_doctor_4"
DOCTOR5 = "demo_doctor_5"


def client_for(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PWD})
    if r.status_code != 200:
        pytest.fail(f"login failed for {email}: {r.status_code} {r.text[:200]}")
    return s


@pytest.fixture(scope="module")
def doctor():
    return client_for("doctor1@careflow.demo")


@pytest.fixture(scope="module")
def doctor2():
    return client_for("doctor2@careflow.demo")


@pytest.fixture(scope="module")
def admin():
    return client_for("admin@careflow.demo")


@pytest.fixture(scope="module")
def patient():
    return client_for("patient@careflow.demo")


@pytest.fixture(scope="module")
def patient1():
    return client_for("patient1@careflow.demo")


def default_schedule(periods_mon_fri=(("09:00", "13:00"), ("14:00", "17:00")), duration=30):
    out = []
    for wd in range(7):
        out.append({"weekday": wd, "periods": [{"start_time": a, "end_time": b} for a, b in periods_mon_fri] if wd < 5 else [], "duration": duration})
    return {"schedule": out}


def next_weekday(target_wd, min_days=7):
    d = date.today() + timedelta(days=min_days)
    while d.weekday() != target_wd:
        d += timedelta(days=1)
    return d.isoformat()


# ---------- sanity ----------
class TestSanity:
    def test_health(self):
        r = requests.get(f"{BASE_URL}/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    @pytest.mark.parametrize("email,role", [
        ("patient@careflow.demo", "patient"),
        ("doctor1@careflow.demo", "doctor"),
        ("admin@careflow.demo", "admin"),
    ])
    def test_seeded_login(self, email, role):
        s = requests.Session()
        r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PWD})
        assert r.status_code == 200
        assert r.json()["role"] == role
        assert "password_hash" not in r.json() and "_id" not in r.json()
        me = s.get(f"{BASE_URL}/api/auth/me")
        assert me.status_code == 200 and me.json()["email"] == email


# ---------- GET/PUT weekly availability ----------
class TestWeeklyAvailability:
    def test_get_shape(self, doctor):
        r = doctor.get(f"{BASE_URL}/api/availability/{DOCTOR1}")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"schedule", "exceptions"}
        assert len(body["schedule"]) == 7
        assert [d["weekday"] for d in body["schedule"]] == list(range(7))
        for d in body["schedule"]:
            assert isinstance(d["periods"], list)
            assert isinstance(d["duration"], int)
            for p in d["periods"]:
                assert set(p.keys()) == {"start_time", "end_time"}
        assert isinstance(body["exceptions"], list)

    def test_get_unknown_doctor_404(self, doctor):
        r = doctor.get(f"{BASE_URL}/api/availability/nope_{RUN}")
        assert r.status_code == 404

    def test_get_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/availability/{DOCTOR1}")
        assert r.status_code == 401

    def test_put_saves_and_get_reflects(self, doctor):
        payload = default_schedule((("08:00", "10:00"),), 20)
        r = doctor.put(f"{BASE_URL}/api/availability/{DOCTOR1}", json=payload)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        g = doctor.get(f"{BASE_URL}/api/availability/{DOCTOR1}").json()
        mon = g["schedule"][0]
        assert mon["periods"] == [{"start_time": "08:00", "end_time": "10:00"}]
        assert mon["duration"] == 20
        assert g["schedule"][5]["periods"] == []
        # restore
        doctor.put(f"{BASE_URL}/api/availability/{DOCTOR1}", json=default_schedule())

    @pytest.mark.parametrize("periods,expected", [
        ([{"start_time": "09:00", "end_time": "12:00"}, {"start_time": "11:00", "end_time": "13:00"}], "overlap"),
        ([{"start_time": "12:00", "end_time": "09:00"}], "start"),
        ([{"start_time": "25:00", "end_time": "26:00"}], "format"),
        ([{"start_time": "9:0", "end_time": "12:00"}], "format"),
    ])
    def test_put_validation_400(self, doctor, periods, expected):
        payload = {"schedule": [{"weekday": 0, "periods": periods, "duration": 30}]}
        r = doctor.put(f"{BASE_URL}/api/availability/{DOCTOR1}", json=payload)
        assert r.status_code == 400, f"{expected}: got {r.status_code} {r.text[:200]}"
        assert isinstance(r.json().get("detail"), str) and r.json()["detail"]

    @pytest.mark.parametrize("duration", [4, 241, 0, -10])
    def test_put_duration_bounds_400(self, doctor, duration):
        payload = {"schedule": [{"weekday": 0, "periods": [{"start_time": "09:00", "end_time": "12:00"}], "duration": duration}]}
        r = doctor.put(f"{BASE_URL}/api/availability/{DOCTOR1}", json=payload)
        assert r.status_code == 400, r.text

    def test_put_invalid_weekday_422(self, doctor):
        r = doctor.put(f"{BASE_URL}/api/availability/{DOCTOR1}", json={"schedule": [{"weekday": 9, "periods": [], "duration": 30}]})
        assert r.status_code == 422

    def test_doctor_cannot_edit_other_doctor(self, doctor):
        r = doctor.put(f"{BASE_URL}/api/availability/{DOCTOR2}", json=default_schedule())
        assert r.status_code == 403

    def test_patient_forbidden(self, patient):
        r = patient.put(f"{BASE_URL}/api/availability/{DOCTOR1}", json=default_schedule())
        assert r.status_code == 403

    def test_admin_can_edit_any(self, admin, doctor2):
        r = admin.put(f"{BASE_URL}/api/availability/{DOCTOR2}", json=default_schedule((("10:00", "12:00"),), 60))
        assert r.status_code == 200
        g = doctor2.get(f"{BASE_URL}/api/availability/{DOCTOR2}").json()
        assert g["schedule"][0]["periods"] == [{"start_time": "10:00", "end_time": "12:00"}]
        assert g["schedule"][0]["duration"] == 60
        admin.put(f"{BASE_URL}/api/availability/{DOCTOR2}", json=default_schedule())


# ---------- slot generation reacts to schedule ----------
class TestSlotGeneration:
    def test_updated_hours_produce_new_slots(self, admin):
        wd = 2
        d = next_weekday(wd)
        admin.put(f"{BASE_URL}/api/availability/{DOCTOR3}", json={"schedule": [{"weekday": wd, "periods": [{"start_time": "07:00", "end_time": "08:00"}], "duration": 30}]})
        r = requests.get(f"{BASE_URL}/api/slots/{DOCTOR3}/{d}")
        assert r.status_code == 200
        assert [x["time"] for x in r.json()] == ["07:00", "07:30"]

    def test_day_marked_unavailable_returns_empty(self, admin):
        wd = 2
        d = next_weekday(wd)
        admin.put(f"{BASE_URL}/api/availability/{DOCTOR3}", json={"schedule": [{"weekday": wd, "periods": [], "duration": 30}]})
        r = requests.get(f"{BASE_URL}/api/slots/{DOCTOR3}/{d}")
        assert r.status_code == 200 and r.json() == []

    def test_two_periods_union_sorted(self, admin):
        wd = 2
        d = next_weekday(wd)
        admin.put(f"{BASE_URL}/api/availability/{DOCTOR3}", json={"schedule": [{"weekday": wd, "periods": [{"start_time": "15:00", "end_time": "16:00"}, {"start_time": "09:00", "end_time": "10:00"}], "duration": 30}]})
        times = [x["time"] for x in requests.get(f"{BASE_URL}/api/slots/{DOCTOR3}/{d}").json()]
        assert times == ["09:00", "09:30", "15:00", "15:30"]
        admin.put(f"{BASE_URL}/api/availability/{DOCTOR3}", json=default_schedule())

    def test_invalid_date_400(self):
        r = requests.get(f"{BASE_URL}/api/slots/{DOCTOR3}/2026-13-99")
        assert r.status_code == 400


# ---------- exceptions / unavailability ----------
class TestExceptions:
    created = []

    def _cleanup(self, session, ex_id):
        session.delete(f"{BASE_URL}/api/availability/exceptions/{ex_id}")

    def test_create_and_shape(self, doctor):
        d = next_weekday(0, min_days=200)
        r = doctor.post(f"{BASE_URL}/api/availability/{DOCTOR1}/exceptions", json={"start_date": d, "end_date": d, "reason": f"TEST_{RUN}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "_id" not in body
        assert body["exception_id"].startswith("ex_")
        assert body["start_date"] == d and body["end_date"] == d
        assert body["reason"] == f"TEST_{RUN}"
        assert isinstance(body["affected"], int)
        # visible in GET
        g = doctor.get(f"{BASE_URL}/api/availability/{DOCTOR1}").json()
        assert any(e["exception_id"] == body["exception_id"] for e in g["exceptions"])
        # slots blocked
        assert requests.get(f"{BASE_URL}/api/slots/{DOCTOR1}/{d}").json() == []
        # delete
        dele = doctor.delete(f"{BASE_URL}/api/availability/exceptions/{body['exception_id']}")
        assert dele.status_code == 200 and dele.json() == {"ok": True}
        g2 = doctor.get(f"{BASE_URL}/api/availability/{DOCTOR1}").json()
        assert not any(e["exception_id"] == body["exception_id"] for e in g2["exceptions"])

    def test_range_blocks_all_dates(self, doctor):
        start = date.today() + timedelta(days=210)
        end = start + timedelta(days=3)
        r = doctor.post(f"{BASE_URL}/api/availability/{DOCTOR1}/exceptions", json={"start_date": start.isoformat(), "end_date": end.isoformat(), "reason": f"TEST_{RUN}"})
        assert r.status_code == 200
        ex_id = r.json()["exception_id"]
        try:
            cur = start
            while cur <= end:
                assert requests.get(f"{BASE_URL}/api/slots/{DOCTOR1}/{cur.isoformat()}").json() == [], cur
                cur += timedelta(days=1)
            after = end + timedelta(days=1)
            # day after block should have slots when it is a weekday with availability
            if after.weekday() < 5:
                assert requests.get(f"{BASE_URL}/api/slots/{DOCTOR1}/{after.isoformat()}").json() != []
        finally:
            self._cleanup(doctor, ex_id)

    def test_invalid_dates_400(self, doctor):
        r = doctor.post(f"{BASE_URL}/api/availability/{DOCTOR1}/exceptions", json={"start_date": "2026-99-99", "end_date": "2026-99-99"})
        assert r.status_code == 400
        r2 = doctor.post(f"{BASE_URL}/api/availability/{DOCTOR1}/exceptions", json={"start_date": "2026-08-10", "end_date": "2026-08-01"})
        assert r2.status_code == 400

    def test_doctor_cannot_block_other(self, doctor):
        d = next_weekday(0, min_days=220)
        r = doctor.post(f"{BASE_URL}/api/availability/{DOCTOR2}/exceptions", json={"start_date": d, "end_date": d})
        assert r.status_code == 403

    def test_admin_can_block_any(self, admin):
        d = next_weekday(0, min_days=230)
        r = admin.post(f"{BASE_URL}/api/availability/{DOCTOR4}/exceptions", json={"start_date": d, "end_date": d, "reason": f"TEST_{RUN}"})
        assert r.status_code == 200
        admin.delete(f"{BASE_URL}/api/availability/exceptions/{r.json()['exception_id']}")

    def test_unknown_doctor_404(self, admin):
        d = next_weekday(0, min_days=240)
        r = admin.post(f"{BASE_URL}/api/availability/nope_{RUN}/exceptions", json={"start_date": d, "end_date": d})
        assert r.status_code == 404

    def test_delete_unknown_404(self, doctor):
        r = doctor.delete(f"{BASE_URL}/api/availability/exceptions/ex_missing_{RUN}")
        assert r.status_code == 404

    def test_patient_cannot_create_or_delete(self, patient):
        d = next_weekday(0, min_days=250)
        assert patient.post(f"{BASE_URL}/api/availability/{DOCTOR1}/exceptions", json={"start_date": d, "end_date": d}).status_code == 403
        assert patient.delete(f"{BASE_URL}/api/availability/exceptions/whatever").status_code == 403


class TestBlockAffectsAppointments:
    def test_confirmed_appointment_flagged_and_patient_notified(self, admin, patient1, doctor2):
        # fresh future weekday for doctor5, own schedule
        wd = 3
        target = next_weekday(wd, min_days=260)
        outside = next_weekday(wd, min_days=290)
        admin.put(f"{BASE_URL}/api/availability/{DOCTOR5}", json={"schedule": [{"weekday": wd, "periods": [{"start_time": "09:00", "end_time": "11:00"}], "duration": 30}]})
        slots = requests.get(f"{BASE_URL}/api/slots/{DOCTOR5}/{target}").json()
        assert slots, "no slots generated for booking setup"
        t1 = next(x["time"] for x in slots if x["available"])
        b1 = patient1.post(f"{BASE_URL}/api/appointments", json={"doctor_id": DOCTOR5, "appointment_date": target, "appointment_time": t1})
        assert b1.status_code == 200, b1.text
        apt1 = b1.json()["appointment_id"]
        slots2 = requests.get(f"{BASE_URL}/api/slots/{DOCTOR5}/{outside}").json()
        t2 = next(x["time"] for x in slots2 if x["available"])
        b2 = patient1.post(f"{BASE_URL}/api/appointments", json={"doctor_id": DOCTOR5, "appointment_date": outside, "appointment_time": t2})
        assert b2.status_code == 200, b2.text
        apt2 = b2.json()["appointment_id"]

        ex = admin.post(f"{BASE_URL}/api/availability/{DOCTOR5}/exceptions", json={"start_date": target, "end_date": target, "reason": f"TEST_{RUN} conference"})
        assert ex.status_code == 200, ex.text
        assert ex.json()["affected"] >= 1
        ex_id = ex.json()["exception_id"]

        appts = {a["appointment_id"]: a for a in patient1.get(f"{BASE_URL}/api/appointments").json()}
        assert appts[apt1]["status"] == "needs_rescheduling"
        assert appts[apt1]["appointment_date"] == target and appts[apt1]["appointment_time"] == t1, "original appointment must be preserved"
        assert appts[apt2]["status"] == "confirmed", "appointment outside range must be untouched"

        notes = patient1.get(f"{BASE_URL}/api/notifications").json()
        match = [n for n in notes if n["title"] == "Appointment needs rescheduling" and target in n["message"] and t1 in n["message"]]
        assert match, f"no reschedule notification with doctor/date/time; got {[n['title'] for n in notes[:5]]}"
        assert "Dr." in match[0]["message"], "notification should include doctor name"

        # slot released -> patient can rebook same slot on another available date
        assert requests.get(f"{BASE_URL}/api/slots/{DOCTOR5}/{target}").json() == []
        admin.delete(f"{BASE_URL}/api/availability/exceptions/{ex_id}")
        freed = requests.get(f"{BASE_URL}/api/slots/{DOCTOR5}/{target}").json()
        assert any(x["time"] == t1 and x["available"] for x in freed), "slot lock should be released for the flagged appointment"
        rebook = patient1.post(f"{BASE_URL}/api/appointments", json={"doctor_id": DOCTOR5, "appointment_date": target, "appointment_time": t1})
        assert rebook.status_code == 200, f"rebooking failed: {rebook.status_code} {rebook.text[:200]}"
        # cleanup
        for a in [apt1, apt2, rebook.json()["appointment_id"]]:
            patient1.patch(f"{BASE_URL}/api/appointments/{a}", json={"status": "cancelled"})
        admin.put(f"{BASE_URL}/api/availability/{DOCTOR5}", json=default_schedule())

    def test_schedule_change_does_not_delete_appointments(self, admin, patient1):
        wd = 4
        target = next_weekday(wd, min_days=300)
        admin.put(f"{BASE_URL}/api/availability/{DOCTOR4}", json={"schedule": [{"weekday": wd, "periods": [{"start_time": "09:00", "end_time": "11:00"}], "duration": 30}]})
        slots = requests.get(f"{BASE_URL}/api/slots/{DOCTOR4}/{target}").json()
        t = next(x["time"] for x in slots if x["available"])
        b = patient1.post(f"{BASE_URL}/api/appointments", json={"doctor_id": DOCTOR4, "appointment_date": target, "appointment_time": t})
        assert b.status_code == 200, b.text
        apt = b.json()["appointment_id"]
        admin.put(f"{BASE_URL}/api/availability/{DOCTOR4}", json={"schedule": [{"weekday": wd, "periods": [{"start_time": "15:00", "end_time": "17:00"}], "duration": 30}]})
        appts = {a["appointment_id"]: a for a in patient1.get(f"{BASE_URL}/api/appointments").json()}
        assert apt in appts, "appointment silently deleted after schedule change"
        assert appts[apt]["status"] == "confirmed"
        patient1.patch(f"{BASE_URL}/api/appointments/{apt}", json={"status": "cancelled"})
        admin.put(f"{BASE_URL}/api/availability/{DOCTOR4}", json=default_schedule())


# ---------- patient documents ----------
class TestPatientDocuments:
    def _pdf(self):
        return b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer<<>>\n%%EOF"

    def test_upload_list_status_and_file_access(self, patient, patient1, doctor, admin):
        content = self._pdf()
        title = f"TEST_{RUN} upload"
        r = patient.post(f"{BASE_URL}/api/patient-documents/upload", data={"title": title},
                         files={"file": ("doc.pdf", io.BytesIO(content), "application/pdf")})
        assert r.status_code == 200, r.text
        doc = r.json()
        assert "_id" not in doc
        assert doc["status"] == "pending_review"
        doc_id = doc["document_id"]

        mine = patient.get(f"{BASE_URL}/api/patient-documents").json()
        assert any(d["document_id"] == doc_id and d["status"] == "pending_review" for d in mine)

        # own file stream
        f = patient.get(f"{BASE_URL}/api/patient-documents/{doc_id}/file")
        assert f.status_code == 200
        assert f.content == content
        assert f.headers.get("content-type", "").startswith("application/pdf")

        # other patient -> 404
        assert patient1.get(f"{BASE_URL}/api/patient-documents/{doc_id}/file").status_code == 404
        # doctor -> 403
        assert doctor.get(f"{BASE_URL}/api/patient-documents/{doc_id}/file").status_code == 403
        # admin -> allowed
        assert admin.get(f"{BASE_URL}/api/patient-documents/{doc_id}/file").status_code == 200
        # unauthenticated -> 401
        assert requests.get(f"{BASE_URL}/api/patient-documents/{doc_id}/file").status_code == 401
        # unknown -> 404
        assert patient.get(f"{BASE_URL}/api/patient-documents/unknown_{RUN}/file").status_code == 404

        # admin review -> accepted reflected for patient
        pr = admin.patch(f"{BASE_URL}/api/patient-documents/{doc_id}", json={"status": "accepted"})
        assert pr.status_code == 200
        mine = patient.get(f"{BASE_URL}/api/patient-documents").json()
        assert next(d for d in mine if d["document_id"] == doc_id)["status"] == "accepted"

        # reject path
        r2 = patient.post(f"{BASE_URL}/api/patient-documents/upload", data={"title": f"TEST_{RUN} reject"},
                          files={"file": ("doc2.pdf", io.BytesIO(content), "application/pdf")})
        doc2 = r2.json()["document_id"]
        assert admin.patch(f"{BASE_URL}/api/patient-documents/{doc2}", json={"status": "rejected"}).status_code == 200
        mine = patient.get(f"{BASE_URL}/api/patient-documents").json()
        assert next(d for d in mine if d["document_id"] == doc2)["status"] == "rejected"

        notes = [n["title"] for n in patient.get(f"{BASE_URL}/api/notifications").json()]
        assert "Document review updated" in notes

    def test_patient_documents_isolation(self, patient, patient1):
        docs = patient1.get(f"{BASE_URL}/api/patient-documents").json()
        assert all(d["patient_id"] == "demo_patient_0" for d in docs), "patient sees other patients' documents"

    def test_upload_rejects_bad_type(self, patient):
        r = patient.post(f"{BASE_URL}/api/patient-documents/upload", data={"title": f"TEST_{RUN} bad"},
                         files={"file": ("x.txt", io.BytesIO(b"hello"), "text/plain")})
        assert r.status_code == 400

    def test_doctor_cannot_upload_patient_document(self, doctor):
        r = doctor.post(f"{BASE_URL}/api/patient-documents/upload", data={"title": "nope"},
                        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")})
        assert r.status_code == 403

    def test_invalid_review_status_400(self, admin, patient):
        r = patient.post(f"{BASE_URL}/api/patient-documents/upload", data={"title": f"TEST_{RUN} status"},
                         files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")})
        doc_id = r.json()["document_id"]
        assert admin.patch(f"{BASE_URL}/api/patient-documents/{doc_id}", json={"status": "bogus"}).status_code == 400
        assert patient.patch(f"{BASE_URL}/api/patient-documents/{doc_id}", json={"status": "accepted"}).status_code == 403


@pytest.fixture(scope="module", autouse=True)
def restore_schedules():
    yield
    a = client_for("admin@careflow.demo")
    for d in [DOCTOR1, DOCTOR2, DOCTOR3, DOCTOR4, DOCTOR5]:
        a.put(f"{BASE_URL}/api/availability/{d}", json=default_schedule())
