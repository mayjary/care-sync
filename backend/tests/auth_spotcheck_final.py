"""Auth playbook spot-checks: bcrypt hash format, httpOnly cookie, CORS creds, brute-force lockout."""
import asyncio, requests, uuid
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import dotenv_values

benv = dotenv_values("/app/backend/.env")
API = dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"].rstrip("/") + "/api"
ORIGIN = dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]


async def db_checks():
    client = AsyncIOMotorClient(benv["MONGO_URL"])
    db = client[benv["DB_NAME"]]
    bad = []
    async for u in db.users.find({}, {"email": 1, "password_hash": 1}):
        if not str(u.get("password_hash", "")).startswith("$2b$"):
            bad.append(u.get("email"))
    print("users with non-$2b$ hash:", bad)
    client.close()


def http_checks():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": "patient@careflow.demo", "password": "CareFlow123!"},
               headers={"Origin": ORIGIN}, timeout=60)
    sc = r.headers.get("set-cookie", "")
    print("login:", r.status_code, "| httpOnly:", "HttpOnly" in sc, "| Secure:", "Secure" in sc,
          "| SameSite:", "samesite=none" in sc.lower())
    print("CORS allow-credentials:", r.headers.get("access-control-allow-credentials"),
          "| allow-origin:", r.headers.get("access-control-allow-origin"))

    ident_email = f"lockprobe_{uuid.uuid4().hex[:6]}@careflow.demo"
    codes = []
    for _ in range(6):
        rr = requests.post(f"{API}/auth/login", json={"email": ident_email, "password": "wrong-pass"},
                           headers={"X-Forwarded-For": "203.0.113.77"}, timeout=60)
        codes.append(rr.status_code)
    print("brute-force codes (expect 401x5 then 429):", codes)
    return ident_email


async def cleanup(email):
    client = AsyncIOMotorClient(benv["MONGO_URL"])
    db = client[benv["DB_NAME"]]
    res = await db.login_attempts.delete_many({"identifier": {"$regex": email}})
    print("login_attempts cleaned:", res.deleted_count)
    client.close()


asyncio.run(db_checks())
email = http_checks()
asyncio.run(cleanup(email))
