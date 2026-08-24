from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr
from dotenv import load_dotenv
from passlib.context import CryptContext
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Optional
import os, uuid, jwt, logging, hashlib, mimetypes, requests, asyncio, re, secrets

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")
def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable {name!r}. Copy backend/.env.example to backend/.env and fill in the values, or run inside the Emergent preview which provisions them automatically.")
    return v
client = AsyncIOMotorClient(_require_env("MONGO_URL"))
db = client[_require_env("DB_NAME")]
app = FastAPI(title="CareFlow API")
api = APIRouter(prefix="/api")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
UPLOAD_DIR = ROOT_DIR / "private_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
SECRET = _require_env("AUTH_SECRET")
reminder_task = None
logger = logging.getLogger("careflow")

APP_NAME = "careflow"
STORAGE_BASE = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"
STORAGE_URL = STORAGE_BASE.rstrip("/") + "/objstore/api/v1/storage"
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")
_storage_key = None
def _init_storage(force: bool = False):
    global _storage_key
    if _storage_key and not force: return _storage_key
    if not EMERGENT_KEY: return None
    r = requests.post(f"{STORAGE_URL}/init", json={"emergent_key": EMERGENT_KEY}, timeout=30)
    r.raise_for_status(); _storage_key = r.json()["storage_key"]; return _storage_key
def storage_available() -> bool:
    try: return bool(_init_storage())
    except Exception as e: logger.warning("Emergent storage unavailable: %s", e); return False
def storage_put(kind: str, owner_id: str, data: bytes, ext: str, content_type: str) -> str:
    key = _init_storage()
    if not key: raise RuntimeError("Storage not initialized")
    path = f"{APP_NAME}/{kind}/{owner_id}/{uuid.uuid4().hex}.{ext or 'bin'}"
    def _put(k):
        return requests.put(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": k, "Content-Type": content_type or "application/octet-stream"}, data=data, timeout=120)
    r = _put(key)
    if r.status_code in (403, 404):
        key = _init_storage(force=True); r = _put(key)
    r.raise_for_status(); return r.json().get("path", path)
def storage_get(path: str) -> tuple[bytes, str]:
    key = _init_storage()
    if not key: raise RuntimeError("Storage not initialized")
    def _get(k): return requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": k}, timeout=60)
    r = _get(key)
    if r.status_code in (403, 404):
        key = _init_storage(force=True); r = _get(key)
    r.raise_for_status(); return r.content, r.headers.get("Content-Type", "application/octet-stream")

def _ext_for(filename: str, content_type: str) -> str:
    if filename and "." in filename: return filename.rsplit(".", 1)[-1].lower()
    return {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}.get(content_type, "bin")

async def save_upload(kind: str, owner_id: str, data: bytes, filename: str, content_type: str) -> dict:
    ext = _ext_for(filename, content_type)
    if storage_available():
        path = storage_put(kind, owner_id, data, ext, content_type)
        return {"storage_backend": "emergent", "storage_path": path}
    fid = "file_" + uuid.uuid4().hex
    (UPLOAD_DIR / fid).write_bytes(data)
    return {"storage_backend": "local", "file_id": fid}

async def read_upload(row: dict) -> tuple[bytes | None, Path | None]:
    if row.get("storage_backend") == "emergent" and row.get("storage_path"):
        try:
            data, _ = storage_get(row["storage_path"]); return data, None
        except Exception as e:
            logger.warning("storage_get failed for %s: %s", row.get("storage_path"), e); return None, None
    fid = row.get("file_id")
    if fid:
        p = UPLOAD_DIR / fid
        if p.exists(): return None, p
    return None, None

app.add_middleware(CORSMiddleware, allow_origin_regex=".*", allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def now(): return datetime.now(timezone.utc)
def public(doc):
    if not doc: return None
    doc = dict(doc); doc.pop("_id", None); doc.pop("password_hash", None); return doc
def token_for(user): return jwt.encode({"sub": user["user_id"], "iat": int(now().timestamp()), "exp": now() + timedelta(days=7)}, SECRET, algorithm="HS256")

class Login(BaseModel): email: EmailStr; password: str
class Register(BaseModel): name: str; email: EmailStr; password: str; role: str = "patient"
class Forgot(BaseModel): email: EmailStr
class Reset(BaseModel): token: str; password: str
class AppointmentCreate(BaseModel): doctor_id: str; appointment_date: str; appointment_time: str
class AvailabilityCreate(BaseModel): doctor_id: Optional[str] = None; weekday: int = Field(ge=0, le=6); start_time: str; end_time: str; duration: int = 30
class Period(BaseModel): start_time: str; end_time: str
class WeeklyDay(BaseModel): weekday: int = Field(ge=0, le=6); periods: list[Period] = []; duration: int = 30
class WeeklySchedule(BaseModel): schedule: list[WeeklyDay]
class ExceptionCreate(BaseModel): start_date: str; end_date: str; reason: Optional[str] = ""
class StatusUpdate(BaseModel): status: str
class NoteCreate(BaseModel): note: str
class ReviewUpdate(BaseModel): status: str

async def current_user(request: Request):
    raw = request.cookies.get("session_token")
    if not raw: raw = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not raw: raise HTTPException(401, "Authentication required")
    try: payload = jwt.decode(raw, SECRET, algorithms=["HS256"])
    except jwt.PyJWTError: raise HTTPException(401, "Session expired")
    user = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0})
    if not user: raise HTTPException(401, "User not found")
    pca = user.get("password_changed_at")
    if pca:
        try:
            pca_ts = int(datetime.fromisoformat(pca).timestamp())
            if payload.get("iat", 0) < pca_ts: raise HTTPException(401, "Session invalidated. Please sign in again.")
        except (ValueError, TypeError): pass
    return user
def roles(*allowed):
    async def check(user=Depends(current_user)):
        if user.get("role") not in allowed: raise HTTPException(403, "You don't have permission for this action")
        return user
    return check

@api.get("/")
async def root(): return {"message": "CareFlow is ready"}
@api.get("/health")
async def health():
    await db.command("ping")
    return {"status": "healthy", "database": "healthy", "storage": "emergent" if storage_available() else "local", "notifications": "active"}

LOCKOUT_MAX = 5
LOCKOUT_WINDOW_SEC = 15 * 60

def _identifier(request: Request, email: str) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    ip = xff.split(",")[0].strip() if xff else (request.client.host if request.client else "unknown")
    return f"{ip}:{email.lower()}"

async def _brute_check(ident: str):
    doc = await db.login_attempts.find_one({"identifier": ident}, {"_id": 0})
    if not doc: return
    if doc.get("count", 0) < LOCKOUT_MAX: return
    last = doc.get("last")
    if isinstance(last, str): last = datetime.fromisoformat(last)
    if last and last.tzinfo is None: last = last.replace(tzinfo=timezone.utc)
    if last and (now() - last).total_seconds() < LOCKOUT_WINDOW_SEC:
        raise HTTPException(429, "Too many failed sign-in attempts. Please wait a few minutes and try again.")

async def _brute_bump(ident: str, success: bool):
    if success:
        await db.login_attempts.delete_one({"identifier": ident})
        return
    doc = await db.login_attempts.find_one({"identifier": ident}, {"_id": 0})
    last = doc.get("last") if doc else None
    if isinstance(last, str): last = datetime.fromisoformat(last)
    if last and last.tzinfo is None: last = last.replace(tzinfo=timezone.utc)
    if last and (now() - last).total_seconds() >= LOCKOUT_WINDOW_SEC:
        await db.login_attempts.update_one({"identifier": ident}, {"$set": {"last": now().isoformat(), "count": 1}}, upsert=True)
    else:
        await db.login_attempts.update_one({"identifier": ident}, {"$set": {"last": now().isoformat()}, "$inc": {"count": 1}}, upsert=True)

@api.post("/auth/register")
async def register(body: Register, response: Response):
    if body.role not in ["patient", "doctor"]: body.role = "patient"
    if len(body.password) < 8: raise HTTPException(400, "Password must be at least 8 characters")
    if await db.users.find_one({"email": body.email.lower()}): raise HTTPException(409, "An account with this email already exists")
    uid = "user_" + uuid.uuid4().hex[:12]
    user = {"user_id": uid, "name": body.name, "email": body.email.lower(), "role": body.role, "password_hash": pwd.hash(body.password), "created_at": now().isoformat()}
    await db.users.insert_one(user); response.set_cookie("session_token", token_for(user), httponly=True, secure=True, samesite="none", max_age=604800, path="/")
    return public(user)
@api.post("/auth/login")
async def login(body: Login, request: Request, response: Response):
    ident = _identifier(request, body.email)
    await _brute_check(ident)
    user = await db.users.find_one({"email": body.email.lower()})
    if not user or not pwd.verify(body.password, user.get("password_hash", "")):
        await _brute_bump(ident, False)
        raise HTTPException(401, "Email or password is incorrect")
    await _brute_bump(ident, True)
    response.set_cookie("session_token", token_for(user), httponly=True, secure=True, samesite="none", max_age=604800, path="/")
    return public(user)
@api.get("/auth/me")
async def me(user=Depends(current_user)): return public(user)
@api.post("/auth/logout")
async def logout(response: Response): response.delete_cookie("session_token", path="/"); return {"ok": True}
@api.post("/auth/forgot-password")
async def forgot_password(body: Forgot):
    user = await db.users.find_one({"email": body.email.lower()}, {"_id": 0})
    if user:
        raw = secrets.token_urlsafe(32)
        expires_at = now() + timedelta(hours=1)
        await db.password_reset_tokens.insert_one({"token": raw, "user_id": user["user_id"], "expires_at": expires_at, "used": False, "created_at": now().isoformat()})
        logger.info("Password reset link (email delivery not configured): /reset-password?token=%s", raw)
        try: await notify(user["user_id"], "Password reset requested", f"Use this link to reset your password: /reset-password?token={raw}", "auth")
        except Exception: pass
    return {"ok": True}
@api.post("/auth/reset-password")
async def reset_password(body: Reset):
    rec = await db.password_reset_tokens.find_one({"token": body.token, "used": False}, {"_id": 0})
    if not rec: raise HTTPException(400, "Invalid or expired reset link")
    exp = rec.get("expires_at")
    if isinstance(exp, str): exp = datetime.fromisoformat(exp)
    if exp and exp.tzinfo is None: exp = exp.replace(tzinfo=timezone.utc)
    if not exp or exp < now(): raise HTTPException(400, "Reset link has expired")
    if len(body.password) < 8: raise HTTPException(400, "Password must be at least 8 characters")
    await db.users.update_one({"user_id": rec["user_id"]}, {"$set": {"password_hash": pwd.hash(body.password), "password_changed_at": now().isoformat()}})
    await db.password_reset_tokens.update_one({"token": body.token}, {"$set": {"used": True, "used_at": now().isoformat()}})
    return {"ok": True}
@api.post("/auth/google")
async def google_auth(session_id: str, response: Response):
    try:
        r = requests.get("https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data", headers={"X-Session-ID": session_id}, timeout=10); data = r.json()
    except Exception: raise HTTPException(502, "Google sign-in could not be completed")
    if not data.get("email"): raise HTTPException(401, "Google sign-in was not completed")
    user = await db.users.find_one({"email": data["email"].lower()})
    if not user:
        user = {"user_id": "user_" + uuid.uuid4().hex[:12], "name": data.get("name", "CareFlow user"), "email": data["email"].lower(), "picture": data.get("picture"), "role": "patient", "created_at": now().isoformat()}; await db.users.insert_one(user)
    response.set_cookie("session_token", token_for(user), httponly=True, secure=True, samesite="none", max_age=604800, path="/"); return public(user)

@api.get("/doctors")
async def doctors(search: str = "", specialty: str = ""):
    q = {"role": "doctor"}
    if search: q["name"] = {"$regex": search, "$options": "i"}
    if specialty: q["specialization"] = specialty
    return [public(x) async for x in db.users.find(q, {"_id": 0, "password_hash": 0})]
@api.get("/doctors/{doctor_id}")
async def doctor(doctor_id: str):
    d = await db.users.find_one({"user_id": doctor_id, "role": "doctor"}, {"_id": 0, "password_hash": 0})
    if not d: raise HTTPException(404, "Doctor not found")
    d["availability"] = [public(x) async for x in db.availability.find({"doctor_id": doctor_id}, {"_id": 0})]; return d
@api.post("/availability")
async def set_availability(body: AvailabilityCreate, user=Depends(roles("doctor", "admin"))):
    doctor_id = user["user_id"] if user["role"] == "doctor" else body.doctor_id
    if not doctor_id or not await db.users.find_one({"user_id": doctor_id, "role": "doctor"}, {"_id": 1}): raise HTTPException(400, "Choose a valid doctor")
    if not TIME_RE.match(body.start_time) or not TIME_RE.match(body.end_time): raise HTTPException(400, "Times must be HH:MM in 24-hour format")
    if body.start_time >= body.end_time: raise HTTPException(400, "Each period must start before it ends")
    if body.duration < 5 or body.duration > 240: raise HTTPException(400, "Appointment length must be between 5 and 240 minutes")
    item = {**body.model_dump(), "availability_id": "av_" + uuid.uuid4().hex[:10], "doctor_id": doctor_id}
    await db.availability.insert_one(item); return public(item)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

def _check_owner(user, doctor_id):
    if user["role"] == "doctor" and user["user_id"] != doctor_id:
        raise HTTPException(403, "You can only manage your own schedule")

@api.get("/availability/{doctor_id}")
async def get_weekly(doctor_id: str, user=Depends(current_user)):
    if not await db.users.find_one({"user_id": doctor_id, "role": "doctor"}, {"_id": 1}): raise HTTPException(404, "Doctor not found")
    rules = [public(x) async for x in db.availability.find({"doctor_id": doctor_id}, {"_id": 0}).sort("start_time", 1)]
    schedule = []
    for wd in range(7):
        day_rules = [r for r in rules if r["weekday"] == wd]
        schedule.append({"weekday": wd, "periods": [{"start_time": r["start_time"], "end_time": r["end_time"]} for r in day_rules], "duration": day_rules[0]["duration"] if day_rules else 30})
    exceptions = [public(x) async for x in db.availability_exceptions.find({"doctor_id": doctor_id}, {"_id": 0}).sort("start_date", 1)]
    return {"schedule": schedule, "exceptions": exceptions}

@api.put("/availability/{doctor_id}")
async def save_weekly(doctor_id: str, body: WeeklySchedule, user=Depends(roles("doctor", "admin"))):
    _check_owner(user, doctor_id)
    if not await db.users.find_one({"user_id": doctor_id, "role": "doctor"}, {"_id": 1}): raise HTTPException(404, "Doctor not found")
    by_weekday = {}
    for day in body.schedule:
        for p in day.periods:
            if not TIME_RE.match(p.start_time) or not TIME_RE.match(p.end_time): raise HTTPException(400, "Times must be HH:MM in 24-hour format")
            if p.start_time >= p.end_time: raise HTTPException(400, "Each period must start before it ends")
        if day.duration < 5 or day.duration > 240: raise HTTPException(400, "Appointment length must be between 5 and 240 minutes")
        by_weekday.setdefault(day.weekday, []).extend([(p.start_time, p.end_time) for p in day.periods])
    for wd, periods in by_weekday.items():
        srt = sorted(periods)
        for i in range(1, len(srt)):
            if srt[i][0] < srt[i - 1][1]: raise HTTPException(400, "Periods on the same day cannot overlap")
    await db.availability.delete_many({"doctor_id": doctor_id})
    for day in body.schedule:
        for p in day.periods:
            await db.availability.insert_one({"availability_id": "av_" + uuid.uuid4().hex[:10], "doctor_id": doctor_id, "weekday": day.weekday, "start_time": p.start_time, "end_time": p.end_time, "duration": day.duration})
    return {"ok": True}

@api.post("/availability/{doctor_id}/exceptions")
async def add_exception(doctor_id: str, body: ExceptionCreate, user=Depends(roles("doctor", "admin"))):
    _check_owner(user, doctor_id)
    if not await db.users.find_one({"user_id": doctor_id, "role": "doctor"}, {"_id": 1}): raise HTTPException(404, "Doctor not found")
    try: s = date.fromisoformat(body.start_date); e = date.fromisoformat(body.end_date)
    except ValueError: raise HTTPException(400, "Invalid dates")
    if s > e: raise HTTPException(400, "Start date must be on or before end date")
    ex = {"exception_id": "ex_" + uuid.uuid4().hex[:10], "doctor_id": doctor_id, "start_date": body.start_date, "end_date": body.end_date, "reason": body.reason or "", "created_at": now().isoformat()}
    await db.availability_exceptions.insert_one(ex)
    ex.pop("_id", None)
    doctor = await db.users.find_one({"user_id": doctor_id}, {"_id": 0})
    doctor_name = (doctor or {}).get("name", "Your doctor")
    affected = await db.appointments.find({"doctor_id": doctor_id, "status": {"$in": ["confirmed", "requested"]}, "appointment_date": {"$gte": body.start_date, "$lte": body.end_date}}, {"_id": 0}).to_list(500)
    for a in affected:
        await db.appointments.update_one({"appointment_id": a["appointment_id"]}, {"$set": {"status": "needs_rescheduling", "unavailability_reason": ex["reason"] or "Doctor unavailable"}})
        await db.slot_locks.delete_one({"appointment_id": a["appointment_id"]})
        await notify(a["patient_id"], "Appointment needs rescheduling", f"Your appointment with {doctor_name} on {a['appointment_date']} at {a['appointment_time']} needs to be rescheduled. Please choose a new time.", "reschedule")
    return {**ex, "affected": len(affected)}

@api.delete("/availability/exceptions/{exception_id}")
async def delete_exception(exception_id: str, user=Depends(roles("doctor", "admin"))):
    ex = await db.availability_exceptions.find_one({"exception_id": exception_id}, {"_id": 0})
    if not ex: raise HTTPException(404, "Unavailability block not found")
    _check_owner(user, ex["doctor_id"])
    await db.availability_exceptions.delete_one({"exception_id": exception_id})
    return {"ok": True}

@api.get("/slots/{doctor_id}/{appointment_date}")
async def slots(doctor_id: str, appointment_date: str):
    try: day = date.fromisoformat(appointment_date)
    except ValueError: raise HTTPException(400, "Invalid date")
    if await db.availability_exceptions.find_one({"doctor_id": doctor_id, "start_date": {"$lte": appointment_date}, "end_date": {"$gte": appointment_date}}, {"_id": 0}): return []
    rules = await db.availability.find({"doctor_id": doctor_id, "weekday": day.weekday()}, {"_id": 0}).to_list(50)
    if not rules: return []
    def mins(v): h, m = map(int, v.split(":")); return h * 60 + m
    booked = await db.appointments.find({"doctor_id": doctor_id, "appointment_date": appointment_date, "status": {"$nin": ["cancelled", "needs_rescheduling"]}}, {"_id": 0, "appointment_time": 1}).to_list(200)
    taken = {x["appointment_time"] for x in booked}
    out = []; seen = set()
    for r in sorted(rules, key=lambda x: x["start_time"]):
        cur = mins(r["start_time"]); end = mins(r["end_time"]); dur = r.get("duration", 30)
        while cur + dur <= end:
            label = f"{cur//60:02d}:{cur%60:02d}"
            if label not in seen:
                seen.add(label); out.append({"time": label, "available": label not in taken})
            cur += dur
    return sorted(out, key=lambda x: x["time"])

async def notify(user_id, title, message, kind="appointment"):
    await db.notifications.insert_one({"notification_id":"nt_"+uuid.uuid4().hex[:10], "user_id":user_id, "title":title, "message":message, "kind":kind, "read":False, "created_at":now().isoformat()})
@api.post("/appointments")
async def book(body: AppointmentCreate, user=Depends(roles("patient"))):
    doctor = await db.users.find_one({"user_id": body.doctor_id, "role":"doctor"}, {"_id":0})
    if not doctor: raise HTTPException(404, "Doctor not found")
    available = await slots(body.doctor_id, body.appointment_date); chosen = next((x for x in available if x["time"] == body.appointment_time), None)
    if not chosen or not chosen["available"]: raise HTTPException(409, "That appointment slot was just taken. Please choose another time.")
    appt={"appointment_id":"apt_"+uuid.uuid4().hex[:10], "patient_id":user["user_id"], "patient_name":user["name"], "doctor_id":body.doctor_id, "doctor_name":doctor["name"], "specialization":doctor.get("specialization",""), "appointment_date":body.appointment_date, "appointment_time":body.appointment_time, "status":"confirmed", "created_at":now().isoformat()}
    lock={"doctor_id":body.doctor_id,"appointment_date":body.appointment_date,"appointment_time":body.appointment_time,"appointment_id":appt["appointment_id"]}
    try: await db.slot_locks.insert_one(lock); await db.appointments.insert_one(appt)
    except Exception:
        await db.slot_locks.delete_one({"appointment_id":appt["appointment_id"]}); raise HTTPException(409, "That appointment slot was just taken. Please choose another time.")
    appointment_dt=datetime.fromisoformat(f"{body.appointment_date}T{body.appointment_time}:00+00:00")
    for lead in [timedelta(hours=24), timedelta(hours=1)]:
        await db.reminders.update_one({"appointment_id":appt["appointment_id"],"lead_hours":int(lead.total_seconds()/3600)}, {"$setOnInsert":{"reminder_id":"rem_"+uuid.uuid4().hex[:10],"appointment_id":appt["appointment_id"],"user_id":user["user_id"],"scheduled_for":(appointment_dt-lead).isoformat(),"lead_hours":int(lead.total_seconds()/3600),"sent":False}}, upsert=True)
    await notify(user["user_id"], "Appointment confirmed", f"Your appointment with {doctor['name']} is confirmed."); await notify(doctor["user_id"], "New appointment", f"{user['name']} booked an appointment for {body.appointment_date}."); return public(appt)
@api.get("/appointments")
async def appointments(user=Depends(current_user)):
    q = {"patient_id":user["user_id"]} if user["role"]=="patient" else ({"doctor_id":user["user_id"]} if user["role"]=="doctor" else {})
    return [public(x) async for x in db.appointments.find(q, {"_id":0}).sort("appointment_date",1)]
@api.patch("/appointments/{appointment_id}")
async def update_appointment(appointment_id: str, body: StatusUpdate, user=Depends(current_user)):
    appt=await db.appointments.find_one({"appointment_id":appointment_id},{"_id":0})
    if not appt: raise HTTPException(404,"Appointment not found")
    if user["role"]=="patient" and appt["patient_id"]!=user["user_id"]: raise HTTPException(403,"Not your appointment")
    if body.status not in ["cancelled","completed","no-show","needs_rescheduling","confirmed"]: raise HTTPException(400,"Invalid status")
    await db.appointments.update_one({"appointment_id":appointment_id},{"$set":{"status":body.status}})
    if body.status == "cancelled": await db.slot_locks.delete_one({"appointment_id":appointment_id})
    await notify(appt["doctor_id"] if user["role"]=="patient" else appt["patient_id"], "Appointment updated", f"Appointment status changed to {body.status}."); return {**appt,"status":body.status}

@api.get("/notifications")
async def notifications(user=Depends(current_user)): return [public(x) async for x in db.notifications.find({"user_id":user["user_id"]},{"_id":0}).sort("created_at",-1).limit(30)]
@api.patch("/notifications/{notification_id}/read")
async def read_notification(notification_id: str, user=Depends(current_user)): await db.notifications.update_one({"notification_id":notification_id,"user_id":user["user_id"]},{"$set":{"read":True}}); return {"ok":True}
@api.get("/records")
async def records(user=Depends(current_user)):
    q={"patient_id":user["user_id"]} if user["role"]=="patient" else ({"doctor_id":user["user_id"]} if user["role"]=="doctor" else {})
    return [public(x) async for x in db.records.find(q,{"_id":0}).sort("created_at",-1)]
@api.post("/records/{patient_id}/notes")
async def add_note(patient_id: str, body: NoteCreate, user=Depends(roles("doctor","admin"))):
    item={"record_id":"rec_"+uuid.uuid4().hex[:10],"patient_id":patient_id,"doctor_id":user["user_id"],"type":"Visit note","title":"Consultation note","description":body.note,"created_at":now().isoformat(),"verified":True}; await db.records.insert_one(item); await notify(patient_id,"Health record updated","A new visit note is available in your health records.","record"); return public(item)
@api.post("/reports/upload")
async def upload_report(patient_id: str=Form(...), title: str=Form(...), report_type: str=Form("Medical report"), visibility: str=Form("private"), file: UploadFile=File(...), user=Depends(roles("admin","doctor"))):
    if file.content_type not in ["application/pdf","image/jpeg","image/png"]: raise HTTPException(400,"Only PDF, JPG, and PNG files are accepted")
    data=await file.read()
    if len(data)>10*1024*1024: raise HTTPException(413,"File must be smaller than 10 MB")
    stored=await save_upload("reports", patient_id, data, file.filename, file.content_type)
    item={"report_id":"rpt_"+uuid.uuid4().hex[:10],"patient_id":patient_id,"uploaded_by":user["user_id"],"uploaded_by_name":user["name"],"title":title,"report_type":report_type,"visibility":"private","file_name":file.filename,"file_size":len(data),"content_type":file.content_type,"created_at":now().isoformat(), **stored}
    await db.reports.insert_one(item); return public(item)
@api.get("/reports")
async def reports(user=Depends(current_user)):
    q={"patient_id":user["user_id"],"visibility":{"$in":["patient_visible","public"]}} if user["role"]=="patient" else ({"uploaded_by":user["user_id"]} if user["role"]=="doctor" else {})
    return [public(x) async for x in db.reports.find(q,{"_id":0}).sort("created_at",-1)]
@api.patch("/reports/{report_id}/visibility")
async def release_report(report_id: str, body: StatusUpdate, user=Depends(roles("admin"))):
    if body.status not in ["private", "patient_visible", "archived"]: raise HTTPException(400, "Invalid report visibility")
    report=await db.reports.find_one({"report_id":report_id},{"_id":0})
    if not report: raise HTTPException(404,"Report not found")
    await db.reports.update_one({"report_id":report_id},{"$set":{"visibility":body.status,"released_at":now().isoformat() if body.status=="patient_visible" else None}})
    if body.status=="patient_visible": await notify(report["patient_id"],"Medical report released","A new medical document is available in your CareFlow account.","report")
    return {**report,"visibility":body.status}
@api.get("/patient-documents")
async def patient_documents(user=Depends(current_user)):
    q={} if user["role"]=="admin" else {"patient_id":user["user_id"]}
    return [public(x) async for x in db.patient_documents.find(q,{"_id":0}).sort("created_at",-1)]
@api.post("/patient-documents/upload")
async def upload_patient_document(title: str=Form(...), file: UploadFile=File(...), user=Depends(roles("patient"))):
    if file.content_type not in ["application/pdf","image/jpeg","image/png"]: raise HTTPException(400,"Only PDF, JPG, and PNG files are accepted")
    data=await file.read()
    if len(data)>10*1024*1024: raise HTTPException(413,"File must be smaller than 10 MB")
    stored=await save_upload("patient-documents", user["user_id"], data, file.filename, file.content_type)
    item={"document_id":"doc_"+uuid.uuid4().hex[:10],"patient_id":user["user_id"],"patient_name":user["name"],"title":title,"file_name":file.filename,"file_size":len(data),"content_type":file.content_type,"status":"pending_review","created_at":now().isoformat(), **stored}
    await db.patient_documents.insert_one(item)
    async for a in db.users.find({"role":"admin"},{"user_id":1}): await notify(a["user_id"],"Patient document awaiting review",f"{user['name']} submitted a document for review.","review")
    return public(item)
@api.patch("/patient-documents/{document_id}")
async def review_patient_document(document_id: str, body: ReviewUpdate, user=Depends(roles("admin"))):
    if body.status not in ["accepted","rejected","needs_clarification"]: raise HTTPException(400,"Invalid review status")
    item=await db.patient_documents.find_one({"document_id":document_id},{"_id":0})
    if not item: raise HTTPException(404,"Patient document not found")
    await db.patient_documents.update_one({"document_id":document_id},{"$set":{"status":body.status,"reviewed_by":user["user_id"],"reviewed_at":now().isoformat()}}); await notify(item["patient_id"],"Document review updated",f"Your uploaded document is now {body.status.replace('_',' ')}.","review"); return {**item,"status":body.status}
@api.get("/reports/{report_id}/file")
async def report_file(report_id: str, user=Depends(current_user)):
    r=await db.reports.find_one({"report_id":report_id},{"_id":0})
    if not r: raise HTTPException(404,"This report is no longer available")
    if user["role"]=="patient" and (r["patient_id"]!=user["user_id"] or r["visibility"] not in ["patient_visible","public"]): raise HTTPException(404,"This report is no longer available")
    if user["role"]=="doctor" and r.get("uploaded_by")!=user["user_id"]: raise HTTPException(403,"You are not authorized to access this report")
    data, path = await read_upload(r)
    if data is not None: return Response(content=data, media_type=r["content_type"], headers={"Content-Disposition": f'inline; filename="{r["file_name"]}"'})
    if path is not None: return FileResponse(path, media_type=r["content_type"], filename=r["file_name"])
    raise HTTPException(404,"This report is no longer available")
@api.get("/patient-documents/{document_id}/file")
async def patient_document_file(document_id: str, user=Depends(current_user)):
    doc = await db.patient_documents.find_one({"document_id": document_id}, {"_id": 0})
    if not doc: raise HTTPException(404, "Document is not available")
    if user["role"] == "patient" and doc["patient_id"] != user["user_id"]: raise HTTPException(404, "Document is not available")
    if user["role"] == "doctor": raise HTTPException(403, "You are not authorized to access this document")
    data, path = await read_upload(doc)
    if data is not None: return Response(content=data, media_type=doc["content_type"], headers={"Content-Disposition": f'inline; filename="{doc["file_name"]}"'})
    if path is not None: return FileResponse(path, media_type=doc["content_type"], filename=doc["file_name"])
    raise HTTPException(404, "Document is not available")
@api.get("/dashboard")
async def dashboard(user=Depends(current_user)):
    q={"patient_id":user["user_id"]} if user["role"]=="patient" else ({"doctor_id":user["user_id"]} if user["role"]=="doctor" else {})
    return {"appointments":await db.appointments.count_documents(q),"reports":await db.reports.count_documents({"patient_id":user["user_id"]}) if user["role"]=="patient" else await db.reports.count_documents({}),"notifications":await db.notifications.count_documents({"user_id":user["user_id"],"read":False}),"patients":await db.users.count_documents({"role":"patient"}) if user["role"]=="admin" else None}

async def reminder_loop():
    while True:
        due=await db.reminders.find({"sent":False,"scheduled_for":{"$lte":now().isoformat()}}).to_list(100)
        for item in due:
            changed=await db.reminders.update_one({"reminder_id":item["reminder_id"],"sent":False},{"$set":{"sent":True,"sent_at":now().isoformat()}})
            if changed.modified_count: await notify(item["user_id"],"Appointment reminder","You have an upcoming appointment in CareFlow.","reminder")
        await asyncio.sleep(60)

async def seed():
    if await db.users.count_documents({}) > 0:
        if await db.patient_documents.count_documents({}) == 0:
            await db.patient_documents.insert_one({"document_id":"demo_doc_1","patient_id":"demo_patient","patient_name":"Aarav Sharma","title":"Previous blood test","file_id":"demo-file","file_name":"previous-blood-test.pdf","file_size":0,"content_type":"application/pdf","status":"pending_review","created_at":now().isoformat()})
        async for d in db.users.find({"role":"doctor"},{"user_id":1}):
            existing_weekdays = await db.availability.distinct("weekday", {"doctor_id": d["user_id"]})
            if len(existing_weekdays) < 5:
                await db.availability.delete_many({"doctor_id": d["user_id"]})
                for wd in range(5):
                    for start, end in [("09:00", "13:00"), ("14:00", "17:00")]:
                        await db.availability.insert_one({"availability_id": "av_" + uuid.uuid4().hex[:10], "doctor_id": d["user_id"], "weekday": wd, "start_time": start, "end_time": end, "duration": 30})
        return
    clinic={"name":"CareFlow Medical Centre","type":"Multi-specialty clinic"}; await db.institutions.insert_one(clinic)
    docs=[("Dr. Sarah Mehta","General Medicine"),("Dr. Rahul Shah","Cardiology"),("Dr. Anika Rao","Dermatology"),("Dr. Vikram Iyer","Orthopedics"),("Dr. Mira Kapoor","Pediatrics")]
    for i,(name,spec) in enumerate(docs):
        uid=f"demo_doctor_{i+1}"; await db.users.insert_one({"user_id":uid,"name":name,"email":f"doctor{i+1}@careflow.demo","role":"doctor","specialization":spec,"experience":f"{8+i} years","institution":"CareFlow Medical Centre","password_hash":pwd.hash("CareFlow123!"),"created_at":now().isoformat()})
        for wd in range(5):
            for start, end in [("09:00", "13:00"), ("14:00", "17:00")]:
                await db.availability.insert_one({"availability_id": "av_" + uuid.uuid4().hex[:10], "doctor_id": uid, "weekday": wd, "start_time": start, "end_time": end, "duration": 30})
    for role,email,name in [("patient","patient@careflow.demo","Aarav Sharma"),("admin","admin@careflow.demo","Clinic Admin")]: await db.users.insert_one({"user_id":"demo_"+role,"name":name,"email":email,"role":role,"password_hash":pwd.hash("CareFlow123!"),"created_at":now().isoformat()})
    for i in range(8): await db.users.insert_one({"user_id":f"demo_patient_{i}","name":f"Demo Patient {i+1}","email":f"patient{i+1}@careflow.demo","role":"patient","password_hash":pwd.hash("CareFlow123!"),"created_at":now().isoformat()})
    await notify("demo_patient","Welcome to CareFlow","Your secure healthcare coordination space is ready.","system")
@app.on_event("startup")
async def startup():
    global reminder_task
    await db.users.create_index("email", unique=True)
    await db.login_attempts.create_index("identifier", unique=True)
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.password_reset_tokens.create_index("token", unique=True)
    await db.slot_locks.create_index([("doctor_id", 1), ("appointment_date", 1), ("appointment_time", 1)], unique=True)
    await db.reminders.create_index([("appointment_id", 1), ("lead_hours", 1)], unique=True)
    await seed()
    try:
        if _init_storage(): logger.info("Emergent object storage initialized")
        else: logger.warning("EMERGENT_LLM_KEY not configured; uploads will use local private_uploads/")
    except Exception as e:
        logger.warning("Emergent storage init failed at startup: %s", e)
    reminder_task=asyncio.create_task(reminder_loop())
@app.on_event("shutdown")
async def shutdown():
    global reminder_task
    if reminder_task: reminder_task.cancel()
    client.close()
app.include_router(api)