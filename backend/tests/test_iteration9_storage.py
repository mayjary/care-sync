"""Iteration 9 — targeted regression for Emergent File & Media Storage integration.

Covers: health storage flag, report upload -> emergent storage_path, byte-exact
download + content-type, visibility/access-control matrix, patient-document upload,
legacy file_id fallback, concurrent upload path uniqueness, rejection paths.
"""
import os
import io
import base64
import subprocess
import concurrent.futures

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base:
    raise RuntimeError("REACT_APP_BACKEND_URL missing from env and /app/frontend/.env")
BASE_URL = base.rstrip("/")
PWD = "CareFlow123!"

MIN_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)

created_reports = []
created_documents = []


def login(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PWD}, timeout=30)
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text[:200]}"
    return s, r.json()


def upload_report(sess, patient_id, title, payload, filename, ctype):
    return sess.post(
        f"{BASE_URL}/api/reports/upload",
        data={"patient_id": patient_id, "title": title, "report_type": "Medical report"},
        files={"file": (filename, io.BytesIO(payload), ctype)},
        timeout=120,
    )


@pytest.fixture(scope="module")
def sessions():
    admin, _ = login("admin@careflow.demo")
    doctor1, _ = login("doctor1@careflow.demo")
    doctor2, _ = login("doctor2@careflow.demo")
    patient, pu = login("patient@careflow.demo")
    patient1, p1u = login("patient1@careflow.demo")
    return {
        "admin": admin, "doctor1": doctor1, "doctor2": doctor2,
        "patient": patient, "patient_id": pu["user_id"],
        "patient1": patient1, "patient1_id": p1u["user_id"],
    }


# --- health ---
def test_health_reports_emergent_storage():
    r = requests.get(f"{BASE_URL}/api/health", timeout=60)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["status"] == "healthy"
    assert d["database"] == "healthy"
    assert d["storage"] == "emergent", f"expected emergent storage, got {d.get('storage')}"


# --- sanity login for each role (no deeper auth retest) ---
@pytest.mark.parametrize("email,role", [
    ("patient@careflow.demo", "patient"),
    ("doctor1@careflow.demo", "doctor"),
    ("admin@careflow.demo", "admin"),
])
def test_role_login_sanity(email, role):
    s, u = login(email)
    assert u["role"] == role
    me = s.get(f"{BASE_URL}/api/auth/me", timeout=30)
    assert me.status_code == 200 and me.json()["email"] == email


# --- report upload stores to Emergent (PDF) + byte-exact download ---
def test_report_upload_pdf_to_emergent(sessions):
    pid = sessions["patient_id"]
    up = upload_report(sessions["admin"], pid, "TEST_iter9_pdf", MIN_PDF, "TEST_iter9.pdf", "application/pdf")
    assert up.status_code == 200, up.text[:400]
    item = up.json()
    created_reports.append(item["report_id"])
    assert item["storage_backend"] == "emergent", item
    assert item["storage_path"].startswith("careflow/reports/"), item["storage_path"]
    assert f"careflow/reports/{pid}/" in item["storage_path"]
    assert item["storage_path"].endswith(".pdf")
    assert "file_id" not in item, "new emergent row must not carry file_id"
    assert "_id" not in item
    assert item["file_size"] == len(MIN_PDF)
    assert item["visibility"] == "private"

    f = sessions["admin"].get(f"{BASE_URL}/api/reports/{item['report_id']}/file", timeout=60)
    assert f.status_code == 200, f.text[:300]
    assert f.content == MIN_PDF, "downloaded bytes differ from uploaded bytes"
    assert f.headers.get("content-type", "").startswith("application/pdf"), f.headers.get("content-type")


# --- report upload PNG content type round-trip ---
def test_report_upload_png_content_type(sessions):
    up = upload_report(sessions["admin"], sessions["patient_id"], "TEST_iter9_png", PNG, "TEST_iter9.png", "image/png")
    assert up.status_code == 200, up.text[:400]
    item = up.json()
    created_reports.append(item["report_id"])
    assert item["storage_backend"] == "emergent"
    assert item["storage_path"].endswith(".png")
    f = sessions["admin"].get(f"{BASE_URL}/api/reports/{item['report_id']}/file", timeout=60)
    assert f.status_code == 200, f.text[:300]
    assert f.content == PNG
    assert f.headers.get("content-type", "").startswith("image/png"), f.headers.get("content-type")


# --- visibility release + access-control matrix ---
def test_visibility_release_access_matrix(sessions):
    pid = sessions["patient_id"]
    up = upload_report(sessions["admin"], pid, "TEST_iter9_release", MIN_PDF, "TEST_iter9_rel.pdf", "application/pdf")
    assert up.status_code == 200, up.text[:400]
    rid = up.json()["report_id"]
    created_reports.append(rid)

    # patient cannot see it while private
    pre = sessions["patient"].get(f"{BASE_URL}/api/reports/{rid}/file", timeout=60)
    assert pre.status_code == 404, f"private report leaked to patient: {pre.status_code}"

    rel = sessions["admin"].patch(f"{BASE_URL}/api/reports/{rid}/visibility", json={"status": "patient_visible"}, timeout=30)
    assert rel.status_code == 200, rel.text[:300]
    assert rel.json()["visibility"] == "patient_visible"

    owner = sessions["patient"].get(f"{BASE_URL}/api/reports/{rid}/file", timeout=60)
    assert owner.status_code == 200, owner.text[:300]
    assert owner.content == MIN_PDF

    other = sessions["patient1"].get(f"{BASE_URL}/api/reports/{rid}/file", timeout=60)
    assert other.status_code == 404, f"other patient got {other.status_code}"

    doc = sessions["doctor2"].get(f"{BASE_URL}/api/reports/{rid}/file", timeout=60)
    assert doc.status_code == 403, f"non-uploader doctor got {doc.status_code}"

    anon = requests.get(f"{BASE_URL}/api/reports/{rid}/file", timeout=30)
    assert anon.status_code == 401, f"anonymous got {anon.status_code}"


# --- patient document upload to Emergent + access matrix ---
def test_patient_document_upload_and_access(sessions):
    up = sessions["patient"].post(
        f"{BASE_URL}/api/patient-documents/upload",
        data={"title": "TEST_iter9_doc"},
        files={"file": ("TEST_iter9_doc.pdf", io.BytesIO(MIN_PDF), "application/pdf")},
        timeout=120,
    )
    assert up.status_code == 200, up.text[:400]
    item = up.json()
    created_documents.append(item["document_id"])
    assert item["storage_backend"] == "emergent", item
    assert item["storage_path"].startswith(f"careflow/patient-documents/{sessions['patient_id']}/"), item["storage_path"]
    assert "file_id" not in item
    assert item["status"] == "pending_review"
    assert item["file_size"] == len(MIN_PDF)

    did = item["document_id"]
    owner = sessions["patient"].get(f"{BASE_URL}/api/patient-documents/{did}/file", timeout=60)
    assert owner.status_code == 200, owner.text[:300]
    assert owner.content == MIN_PDF
    assert owner.headers.get("content-type", "").startswith("application/pdf")

    other = sessions["patient1"].get(f"{BASE_URL}/api/patient-documents/{did}/file", timeout=60)
    assert other.status_code == 404, f"other patient got {other.status_code}"

    doc = sessions["doctor1"].get(f"{BASE_URL}/api/patient-documents/{did}/file", timeout=60)
    assert doc.status_code == 403, f"doctor got {doc.status_code}"

    adm = sessions["admin"].get(f"{BASE_URL}/api/patient-documents/{did}/file", timeout=60)
    assert adm.status_code == 200, adm.text[:300]
    assert adm.content == MIN_PDF


# --- legacy rows (file_id, no storage_path) still served from local disk ---
def test_legacy_file_id_rows_still_download(sessions):
    listing = sessions["admin"].get(f"{BASE_URL}/api/reports", timeout=30)
    assert listing.status_code == 200
    legacy = [x for x in listing.json() if x.get("file_id") and not x.get("storage_path")]
    if not legacy:
        pytest.skip("no legacy file_id reports present")
    checked = 0
    for row in legacy[:3]:
        f = sessions["admin"].get(f"{BASE_URL}/api/reports/{row['report_id']}/file", timeout=60)
        assert f.status_code == 200, f"legacy {row['report_id']} -> {f.status_code} {f.text[:200]}"
        assert len(f.content) > 0
        checked += 1
    assert checked > 0


# --- concurrent uploads get distinct storage paths ---
def test_concurrent_uploads_distinct_paths(sessions):
    pid = sessions["patient_id"]

    def do(n):
        return upload_report(sessions["admin"], pid, f"TEST_iter9_conc_{n}", MIN_PDF + str(n).encode(), f"TEST_c{n}.pdf", "application/pdf")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(do, [1, 2]))
    paths = []
    for r in results:
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        created_reports.append(j["report_id"])
        assert j["storage_backend"] == "emergent"
        paths.append(j["storage_path"])
    assert len(set(paths)) == 2, f"storage path collision: {paths}"
    # byte-exactness preserved per object
    for r, n in zip(results, [1, 2]):
        got = sessions["admin"].get(f"{BASE_URL}/api/reports/{r.json()['report_id']}/file", timeout=60)
        assert got.status_code == 200
        assert got.content == MIN_PDF + str(n).encode()


# --- rejection paths ---
def test_reject_bad_content_type(sessions):
    r = upload_report(sessions["admin"], sessions["patient_id"], "TEST_iter9_bad", b"hello", "TEST_bad.txt", "text/plain")
    assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text[:200]}"
    d = sessions["patient"].post(
        f"{BASE_URL}/api/patient-documents/upload",
        data={"title": "TEST_iter9_bad"},
        files={"file": ("TEST_bad.txt", io.BytesIO(b"hello"), "text/plain")},
        timeout=60,
    )
    assert d.status_code == 400, f"expected 400, got {d.status_code} {d.text[:200]}"


def test_reject_oversize_file(sessions):
    big = b"%PDF-1.4\n" + b"0" * (10 * 1024 * 1024 + 10)
    r = upload_report(sessions["admin"], sessions["patient_id"], "TEST_iter9_big", big, "TEST_big.pdf", "application/pdf")
    assert r.status_code == 413, f"expected 413, got {r.status_code} {r.text[:200]}"


# --- cleanup Mongo rows created by this run ---
def test_zz_cleanup():
    if not created_reports and not created_documents:
        pytest.skip("nothing to clean")
    script = (
        "import asyncio,os\n"
        "from motor.motor_asyncio import AsyncIOMotorClient\n"
        "from dotenv import load_dotenv\n"
        "load_dotenv('/app/backend/.env')\n"
        f"reps={created_reports!r}\ndocs={created_documents!r}\n"
        "async def main():\n"
        "    c=AsyncIOMotorClient(os.environ['MONGO_URL']); db=c[os.environ['DB_NAME']]\n"
        "    r1=await db.reports.delete_many({'report_id':{'$in':reps}})\n"
        "    r2=await db.patient_documents.delete_many({'document_id':{'$in':docs}})\n"
        "    print('cleaned',r1.deleted_count,r2.deleted_count)\n"
        "asyncio.run(main())\n"
    )
    out = subprocess.run(["python", "-c", script], capture_output=True, text=True)
    print(out.stdout, out.stderr)
    assert "cleaned" in out.stdout
    s, _ = login("admin@careflow.demo")
    listing = s.get(f"{BASE_URL}/api/reports", timeout=30).json()
    assert all(x["report_id"] not in created_reports for x in listing)
