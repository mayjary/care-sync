"""Iteration 5: clinic report upload/release + patient-document review regression."""
import os
import re
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
PASSWORD = "CareFlow123!"
PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF CAREFLOW_TEST_CONTENT"
import uuid as _uuid
RUN = "TEST_" + _uuid.uuid4().hex[:6] + "_"  # process-unique prefix so parallel workers do not delete each other data


def login(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PASSWORD})
    if r.status_code != 200:
        pytest.fail(f"login failed for {email}: {r.status_code} {r.text[:200]}")
    return s, r.json()


@pytest.fixture(scope="module")
def credentials_file():
    p = Path("/app/memory/test_credentials.md")
    if not p.exists():
        pytest.skip("missing test_credentials.md")
    return p.read_text()


@pytest.fixture(scope="module")
def admin():
    return login("admin@careflow.demo")


@pytest.fixture(scope="module")
def patient():
    return login("patient@careflow.demo")


@pytest.fixture(scope="module")
def other_patient():
    return login("patient1@careflow.demo")


# --- Sanity / auth ---
class TestSanity:
    def test_health(self):
        r = requests.get(f"{BASE_URL}/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"
        assert r.json()["database"] == "healthy"

    @pytest.mark.parametrize("email,role", [
        ("patient@careflow.demo", "patient"),
        ("doctor1@careflow.demo", "doctor"),
        ("admin@careflow.demo", "admin"),
    ])
    def test_login_and_me_role(self, email, role):
        s, user = login(email)
        assert user["role"] == role
        assert "password_hash" not in user and "_id" not in user
        me = s.get(f"{BASE_URL}/api/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == role
        assert me.json()["email"] == email

    @pytest.mark.parametrize("path", ["/api/auth/me", "/api/reports", "/api/patient-documents", "/api/appointments", "/api/notifications", "/api/dashboard"])
    def test_protected_requires_auth(self, path):
        r = requests.get(f"{BASE_URL}{path}")
        assert r.status_code == 401, f"{path} -> {r.status_code}"

    def test_credentials_file_present(self, credentials_file):
        assert "admin@careflow.demo" in credentials_file


# --- Report upload / release / access control ---
class TestReportWorkflow:
    def test_full_report_release_flow(self, admin, patient, other_patient):
        admin_s, admin_u = admin
        patient_s, patient_u = patient
        other_s, other_u = other_patient
        pid = patient_u["user_id"]

        # upload private report
        r = admin_s.post(f"{BASE_URL}/api/reports/upload",
                         data={"patient_id": pid, "title": RUN + "Blood panel", "report_type": "Blood test"},
                         files={"file": ("TEST_report.pdf", PDF_BYTES, "application/pdf")})
        assert r.status_code == 200, r.text[:300]
        rep = r.json()
        assert rep["visibility"] == "private"
        assert rep["patient_id"] == pid
        assert rep["title"] == RUN + "Blood panel"
        assert rep["file_size"] == len(PDF_BYTES)
        assert "_id" not in rep
        rid = rep["report_id"]

        # admin list contains it
        alist = admin_s.get(f"{BASE_URL}/api/reports")
        assert alist.status_code == 200
        assert any(x["report_id"] == rid and x["visibility"] == "private" for x in alist.json())

        # patient must NOT see private report
        plist = patient_s.get(f"{BASE_URL}/api/reports")
        assert plist.status_code == 200
        assert all(x["report_id"] != rid for x in plist.json()), "private report leaked to patient list"

        # patient must NOT stream private file
        assert patient_s.get(f"{BASE_URL}/api/reports/{rid}/file").status_code in (403, 404)

        # release
        rel = admin_s.patch(f"{BASE_URL}/api/reports/{rid}/visibility", json={"status": "patient_visible"})
        assert rel.status_code == 200, rel.text[:300]
        assert rel.json()["visibility"] == "patient_visible"

        # patient now sees it
        plist2 = patient_s.get(f"{BASE_URL}/api/reports")
        match = [x for x in plist2.json() if x["report_id"] == rid]
        assert match, "released report not visible to patient"
        assert match[0]["visibility"] == "patient_visible"

        # patient can stream file
        f = patient_s.get(f"{BASE_URL}/api/reports/{rid}/file")
        assert f.status_code == 200, f.status_code
        assert f.content == PDF_BYTES

        # other patient cannot see or stream
        olist = other_s.get(f"{BASE_URL}/api/reports")
        assert all(x["report_id"] != rid for x in olist.json())
        assert other_s.get(f"{BASE_URL}/api/reports/{rid}/file").status_code in (403, 404)

        # unauthenticated cannot stream
        assert requests.get(f"{BASE_URL}/api/reports/{rid}/file").status_code == 401

        # notification created for patient
        notes = patient_s.get(f"{BASE_URL}/api/notifications")
        assert notes.status_code == 200
        assert any(n["kind"] == "report" for n in notes.json()), "no report release notification"

    def test_patient_cannot_upload_report(self, patient):
        s, _ = patient
        r = s.post(f"{BASE_URL}/api/reports/upload",
                   data={"patient_id": "demo_patient", "title": RUN + "x"},
                   files={"file": ("a.pdf", PDF_BYTES, "application/pdf")})
        assert r.status_code == 403

    def test_reject_invalid_file_type(self, admin):
        s, _ = admin
        r = s.post(f"{BASE_URL}/api/reports/upload",
                   data={"patient_id": "demo_patient", "title": RUN + "bad"},
                   files={"file": ("a.txt", b"hello", "text/plain")})
        assert r.status_code == 400

    def test_invalid_visibility_and_missing_report(self, admin):
        s, _ = admin
        rid = [x["report_id"] for x in s.get(f"{BASE_URL}/api/reports").json()]
        assert rid, "no reports available for negative test"
        bad = s.patch(f"{BASE_URL}/api/reports/{rid[0]}/visibility", json={"status": "nonsense"})
        assert bad.status_code == 400
        missing = s.patch(f"{BASE_URL}/api/reports/rpt_doesnotexist/visibility", json={"status": "patient_visible"})
        assert missing.status_code == 404

    def test_patient_cannot_release(self, patient):
        s, _ = patient
        r = s.patch(f"{BASE_URL}/api/reports/rpt_whatever/visibility", json={"status": "patient_visible"})
        assert r.status_code == 403


# --- Patient document submission + admin review ---
class TestPatientDocuments:
    def test_upload_and_review_flow(self, patient, admin, other_patient):
        p_s, p_u = patient
        a_s, _ = admin
        o_s, _ = other_patient

        up = p_s.post(f"{BASE_URL}/api/patient-documents/upload",
                      data={"title": RUN + "Old prescription"},
                      files={"file": ("TEST_doc.png", PDF_BYTES, "image/png")})
        assert up.status_code == 200, up.text[:300]
        doc = up.json()
        assert doc["status"] == "pending_review"
        assert doc["patient_id"] == p_u["user_id"]
        assert "_id" not in doc
        did = doc["document_id"]

        # admin queue shows pending doc
        queue = a_s.get(f"{BASE_URL}/api/patient-documents")
        assert queue.status_code == 200
        q = [x for x in queue.json() if x["document_id"] == did]
        assert q and q[0]["status"] == "pending_review"

        # other patient must not see it
        assert all(x["document_id"] != did for x in o_s.get(f"{BASE_URL}/api/patient-documents").json())

        # patient cannot review
        assert p_s.patch(f"{BASE_URL}/api/patient-documents/{did}", json={"status": "accepted"}).status_code == 403

        # admin accepts
        rev = a_s.patch(f"{BASE_URL}/api/patient-documents/{did}", json={"status": "accepted"})
        assert rev.status_code == 200
        assert rev.json()["status"] == "accepted"
        after = [x for x in a_s.get(f"{BASE_URL}/api/patient-documents").json() if x["document_id"] == did]
        assert after[0]["status"] == "accepted"
        assert after[0].get("reviewed_by")

        # reject flow on a second doc
        up2 = p_s.post(f"{BASE_URL}/api/patient-documents/upload",
                       data={"title": RUN + "Reject me"},
                       files={"file": ("TEST_doc2.pdf", PDF_BYTES, "application/pdf")})
        did2 = up2.json()["document_id"]
        rej = a_s.patch(f"{BASE_URL}/api/patient-documents/{did2}", json={"status": "rejected"})
        assert rej.status_code == 200 and rej.json()["status"] == "rejected"

        # invalid status + unknown doc
        assert a_s.patch(f"{BASE_URL}/api/patient-documents/{did2}", json={"status": "bogus"}).status_code == 400
        assert a_s.patch(f"{BASE_URL}/api/patient-documents/doc_missing", json={"status": "accepted"}).status_code == 404

        # patient notified of review
        notes = p_s.get(f"{BASE_URL}/api/notifications").json()
        assert any(n["kind"] == "review" for n in notes), "no review notification for patient"

    def test_admin_receives_pending_notification(self, patient, admin):
        p_s, p_u = patient
        a_s, _ = admin
        p_s.post(f"{BASE_URL}/api/patient-documents/upload",
                 data={"title": RUN + "Notify admin"},
                 files={"file": ("TEST_n.pdf", PDF_BYTES, "application/pdf")})
        notes = a_s.get(f"{BASE_URL}/api/notifications").json()
        assert any(n["kind"] == "review" for n in notes), "admin not notified about pending patient document"


# --- Cleanup of TEST_ data ---
@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    try:
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        env = dotenv_values("/app/backend/.env")

        async def run():
            c = AsyncIOMotorClient(env["MONGO_URL"])
            d = c[env["DB_NAME"]]
            await d.reports.delete_many({"title": {"$regex": "^" + RUN}})
            await d.patient_documents.delete_many({"title": {"$regex": "^" + RUN}})
            c.close()
        asyncio.get_event_loop().run_until_complete(run()) if False else asyncio.run(run())
    except Exception as e:  # cleanup best-effort
        print("cleanup skipped:", e)
