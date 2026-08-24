"""Setup/teardown helper for the needs_rescheduling UI check (TEST 5 UI)."""
import sys, json, datetime, requests
from dotenv import dotenv_values
API = dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"].rstrip("/") + "/api"
STATE = "/tmp/reschedule_ui_state.json"

def s_login(email):
    s = requests.Session(); s.headers.update({"X-Forwarded-For": "203.0.113.9"})
    r = s.post(f"{API}/auth/login", json={"email": email, "password": "CareFlow123!"}, timeout=60)
    assert r.status_code == 200, r.text
    return s

if sys.argv[1] == "setup":
    p = s_login("patient1@careflow.demo")
    d = datetime.date.today() + datetime.timedelta(days=60)
    while d.weekday() != 0:
        d += datetime.timedelta(days=1)
    day = d.isoformat()
    free = [x for x in p.get(f"{API}/slots/demo_doctor_1/{day}", timeout=60).json() if x["available"]]
    a = p.post(f"{API}/appointments", json={"doctor_id": "demo_doctor_1", "appointment_date": day,
                                            "appointment_time": free[0]["time"]}, timeout=60).json()
    doc = s_login("doctor1@careflow.demo")
    ex = doc.post(f"{API}/availability/demo_doctor_1/exceptions",
                  json={"start_date": day, "end_date": day, "reason": "TEST_ui_reschedule"}, timeout=60).json()
    json.dump({"appointment_id": a["appointment_id"], "exception_id": ex["exception_id"], "date": day},
              open(STATE, "w"))
    print("setup:", a["appointment_id"], day, "affected:", ex["affected"])
else:
    st = json.load(open(STATE))
    doc = s_login("doctor1@careflow.demo")
    print("del exception:", doc.delete(f"{API}/availability/exceptions/{st['exception_id']}", timeout=60).status_code)
    p = s_login("patient1@careflow.demo")
    print("cancel appt:", p.patch(f"{API}/appointments/{st['appointment_id']}",
                                  json={"status": "cancelled"}, timeout=60).status_code)
