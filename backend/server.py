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
import os, uuid, jwt, logging, hashlib, mimetypes, requests, asyncio

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")
client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
app = FastAPI(title="CareFlow API")
api = APIRouter(prefix="/api")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
UPLOAD_DIR = ROOT_DIR / "private_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
SECRET = os.environ["AUTH_SECRET"]
reminder_task = None

app.add_middleware(CORSMiddleware, allow_origin_regex=".*", allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def now(): return datetime.now(timezone.utc)
def public(doc):
    if not doc: return None
    doc = dict(doc); doc.pop("_id", None); doc.pop("password_hash", None); return doc
def token_for(user): return jwt.encode({"sub": user["user_id"], "exp": now() + timedelta(days=7)}, SECRET, algorithm="HS256")

class Login(BaseModel): email: EmailStr; password: str
class Register(BaseModel): name: str; email: EmailStr; password: str; role: str = "patient"
class AppointmentCreate(BaseModel): doctor_id: str; appointment_date: str; appointment_time: str
class AvailabilityCreate(BaseModel): doctor_id: Optional[str] = None; weekday: int = Field(ge=0, le=6); start_time: str; end_time: str; duration: int = 30
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
    await db.command("ping"); return {"status": "healthy", "database": "healthy", "storage": "healthy", "notifications": "active"}

@api.post("/auth/register")
async def register(body: Register, response: Response):
    if body.role not in ["patient", "doctor"]: body.role = "patient"
    if await db.users.find_one({"email": body.email.lower()}): raise HTTPException(409, "An account with this email already exists")
    uid = "user_" + uuid.uuid4().hex[:12]
    user = {"user_id": uid, "name": body.name, "email": body.email.lower(), "role": body.role, "password_hash": pwd.hash(body.password), "created_at": now().isoformat()}
    await db.users.insert_one(user); response.set_cookie("session_token", token_for(user), httponly=True, secure=True, samesite="none", max_age=604800, path="/")
    return public(user)
@api.post("/auth/login")
async def login(body: Login, response: Response):
    user = await db.users.find_one({"email": body.email.lower()})
    if not user or not pwd.verify(body.password, user.get("password_hash", "")): raise HTTPException(401, "Email or password is incorrect")
    response.set_cookie("session_token", token_for(user), httponly=True, secure=True, samesite="none", max_age=604800, path="/")
    return public(user)
@api.get("/auth/me")
async def me(user=Depends(current_user)): return public(user)
@api.post("/auth/logout")
async def logout(response: Response): response.delete_cookie("session_token", path="/"); return {"ok": True}
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
    item = {"availability_id": "av_" + uuid.uuid4().hex[:10], "doctor_id": doctor_id, **body.model_dump()}; await db.availability.update_one({"doctor_id": doctor_id, "weekday": body.weekday}, {"$set": item}, upsert=True); return item
@api.get("/slots/{doctor_id}/{appointment_date}")
async def slots(doctor_id: str, appointment_date: str):
    try: day = date.fromisoformat(appointment_date)
    except ValueError: raise HTTPException(400, "Invalid date")
    rule = await db.availability.find_one({"doctor_id": doctor_id, "weekday": day.weekday()}, {"_id": 0})
    if not rule: return []
    def mins(v): h,m=map(int,v.split(":")); return h*60+m
    booked = await db.appointments.find({"doctor_id": doctor_id, "appointment_date": appointment_date, "status": {"$nin": ["cancelled"]}}, {"_id": 0, "appointment_time": 1}).to_list(100)
    taken = {x["appointment_time"] for x in booked}; out=[]; cur=mins(rule["start_time"]); end=mins(rule["end_time"])
    while cur + rule["duration"] <= end:
        label=f"{cur//60:02d}:{cur%60:02d}"; out.append({"time":label,"available":label not in taken}); cur += rule["duration"]
    return out

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
    fid="file_"+uuid.uuid4().hex; path=UPLOAD_DIR/fid; path.write_bytes(data)
    item={"report_id":"rpt_"+uuid.uuid4().hex[:10],"patient_id":patient_id,"uploaded_by":user["user_id"],"uploaded_by_name":user["name"],"title":title,"report_type":report_type,"visibility":"private","file_id":fid,"file_name":file.filename,"file_size":len(data),"content_type":file.content_type,"created_at":now().isoformat()}; await db.reports.insert_one(item); return public(item)
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
    fid="file_"+uuid.uuid4().hex; (UPLOAD_DIR/fid).write_bytes(data)
    item={"document_id":"doc_"+uuid.uuid4().hex[:10],"patient_id":user["user_id"],"patient_name":user["name"],"title":title,"file_id":fid,"file_name":file.filename,"file_size":len(data),"content_type":file.content_type,"status":"pending_review","created_at":now().isoformat()}; await db.patient_documents.insert_one(item)
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
    path=UPLOAD_DIR/r["file_id"]
    if not path.exists(): raise HTTPException(404,"This report is no longer available")
    return FileResponse(path, media_type=r["content_type"], filename=r["file_name"])
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
        return
    clinic={"name":"CareFlow Medical Centre","type":"Multi-specialty clinic"}; await db.institutions.insert_one(clinic)
    docs=[("Dr. Sarah Mehta","General Medicine"),("Dr. Rahul Shah","Cardiology"),("Dr. Anika Rao","Dermatology"),("Dr. Vikram Iyer","Orthopedics"),("Dr. Mira Kapoor","Pediatrics")]
    for i,(name,spec) in enumerate(docs):
        uid=f"demo_doctor_{i+1}"; await db.users.insert_one({"user_id":uid,"name":name,"email":f"doctor{i+1}@careflow.demo","role":"doctor","specialization":spec,"experience":f"{8+i} years","institution":"CareFlow Medical Centre","password_hash":pwd.hash("CareFlow123!"),"created_at":now().isoformat()}); await db.availability.insert_one({"availability_id":"av_"+uuid.uuid4().hex[:10],"doctor_id":uid,"weekday":i%5,"start_time":"09:00","end_time":"17:00","duration":30})
    for role,email,name in [("patient","patient@careflow.demo","Aarav Sharma"),("admin","admin@careflow.demo","Clinic Admin")]: await db.users.insert_one({"user_id":"demo_"+role,"name":name,"email":email,"role":role,"password_hash":pwd.hash("CareFlow123!"),"created_at":now().isoformat()})
    for i in range(8): await db.users.insert_one({"user_id":f"demo_patient_{i}","name":f"Demo Patient {i+1}","email":f"patient{i+1}@careflow.demo","role":"patient","password_hash":pwd.hash("CareFlow123!"),"created_at":now().isoformat()})
    await notify("demo_patient","Welcome to CareFlow","Your secure healthcare coordination space is ready.","system")
@app.on_event("startup")
async def startup():
    global reminder_task
    await db.slot_locks.create_index([("doctor_id", 1), ("appointment_date", 1), ("appointment_time", 1)], unique=True)
    await db.reminders.create_index([("appointment_id", 1), ("lead_hours", 1)], unique=True)
    await seed()
    reminder_task=asyncio.create_task(reminder_loop())
@app.on_event("shutdown")
async def shutdown():
    global reminder_task
    if reminder_task: reminder_task.cancel()
    client.close()
app.include_router(api)