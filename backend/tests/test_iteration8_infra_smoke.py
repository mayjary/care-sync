"""Iteration 8 — infra/config audit smoke tests (targeted, 9 assertions only)."""
import os
import io
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

created_report_ids = []


def login(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PWD}, timeout=30)
    return s, r


# --- (1) health ---
def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=30)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["status"] == "healthy"
    assert d["database"] == "healthy"


# --- (2)(3)(4) logins + cookie session via /api/auth/me ---
@pytest.mark.parametrize("email,role", [
    ("patient@careflow.demo", "patient"),
    ("doctor1@careflow.demo", "doctor"),
    ("admin@careflow.demo", "admin"),
])
def test_login_and_session(email, role):
    s, r = login(email)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["email"] == email
    assert r.json()["role"] == role
    assert "password_hash" not in r.json()
    assert "_id" not in r.json()
    assert "session_token" in s.cookies.get_dict(), s.cookies.get_dict()
    me = s.get(f"{BASE_URL}/api/auth/me", timeout=30)
    assert me.status_code == 200, me.text[:300]
    assert me.json()["email"] == email
    assert me.json()["role"] == role


# --- (5) GET /api/reports as admin ---
def test_reports_list_admin():
    s, r = login("admin@careflow.demo")
    assert r.status_code == 200
    rep = s.get(f"{BASE_URL}/api/reports", timeout=30)
    assert rep.status_code == 200, rep.text[:300]
    assert isinstance(rep.json(), list)
    for item in rep.json():
        assert "_id" not in item


# --- (6)(7) upload + authorized/unauthorized file access ---
def test_report_upload_and_access_control():
    admin, ra = login("admin@careflow.demo")
    assert ra.status_code == 200
    # target patient = patient1@careflow.demo (not patient@careflow.demo)
    doctors = requests.get(f"{BASE_URL}/api/doctors", timeout=30).json()
    assert len(doctors) >= 2

    up = admin.post(
        f"{BASE_URL}/api/reports/upload",
        data={"patient_id": "demo_patient_0", "title": "TEST_iter8_smoke", "report_type": "Medical report"},
        files={"file": ("TEST_iter8.pdf", io.BytesIO(MIN_PDF), "application/pdf")},
        timeout=60,
    )
    assert up.status_code == 200, up.text[:400]
    item = up.json()
    assert "_id" not in item
    rid = item["report_id"]
    created_report_ids.append(rid)
    assert item["visibility"] == "private"
    assert item["file_size"] == len(MIN_PDF)

    # (6) uploader/admin can fetch the file
    f = admin.get(f"{BASE_URL}/api/reports/{rid}/file", timeout=30)
    assert f.status_code == 200, f.text[:300]
    assert f.content == MIN_PDF
    assert "pdf" in f.headers.get("content-type", "")

    # (7a) patient with no released report -> 404
    pat, rp = login("patient@careflow.demo")
    assert rp.status_code == 200
    pf = pat.get(f"{BASE_URL}/api/reports/{rid}/file", timeout=30)
    assert pf.status_code == 404, f"expected 404, got {pf.status_code}: {pf.text[:300]}"

    # (7b) doctor who did not upload -> 403
    doc, rd = login("doctor2@careflow.demo")
    assert rd.status_code == 200
    df = doc.get(f"{BASE_URL}/api/reports/{rid}/file", timeout=30)
    assert df.status_code == 403, f"expected 403, got {df.status_code}: {df.text[:300]}"

    # unauthenticated -> 401
    ua = requests.get(f"{BASE_URL}/api/reports/{rid}/file", timeout=30)
    assert ua.status_code == 401


def test_zz_cleanup_test_reports():
    """Remove the test upload rows/files created above (no product delete endpoint)."""
    import subprocess
    if not created_report_ids:
        pytest.skip("nothing to clean")
    script = (
        "import asyncio,os\n"
        "from pathlib import Path\n"
        "from motor.motor_asyncio import AsyncIOMotorClient\n"
        "from dotenv import load_dotenv\n"
        "load_dotenv('/app/backend/.env')\n"
        "ids=" + repr(created_report_ids) + "\n"
        "async def main():\n"
        "    c=AsyncIOMotorClient(os.environ['MONGO_URL']); db=c[os.environ['DB_NAME']]\n"
        "    for rid in ids:\n"
        "        r=await db.reports.find_one({'report_id':rid},{'_id':0})\n"
        "        if r:\n"
        "            p=Path('/app/backend/private_uploads')/r['file_id']\n"
        "            if p.exists(): p.unlink()\n"
        "            await db.reports.delete_one({'report_id':rid})\n"
        "    print('cleaned',ids)\n"
        "asyncio.run(main())\n"
    )
    out = subprocess.run(["python", "-c", script], capture_output=True, text=True)
    print(out.stdout, out.stderr)
    assert "cleaned" in out.stdout

    s, _ = login("admin@careflow.demo")
    listing = s.get(f"{BASE_URL}/api/reports", timeout=30).json()
    for rid in created_report_ids:
        assert all(x["report_id"] != rid for x in listing)
