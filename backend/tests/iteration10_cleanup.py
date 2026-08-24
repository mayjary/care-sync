"""Cleanup iteration 10 test artefacts and verify demo credentials are intact."""
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

fe = dotenv_values("/app/frontend/.env")
be = dotenv_values("/app/backend/.env")
API = fe["REACT_APP_BACKEND_URL"].rstrip("/") + "/api"
db = MongoClient(be["MONGO_URL"])[be["DB_NAME"]]

test_users = list(db.users.find({"email": {"$regex": "^(test_|TEST_)"}}, {"_id": 0, "user_id": 1, "email": 1}))
ids = [u["user_id"] for u in test_users]
print("test users to delete:", [u["email"] for u in test_users])
db.users.delete_many({"user_id": {"$in": ids}})
db.notifications.delete_many({"user_id": {"$in": ids}})
print("reset tokens removed:", db.password_reset_tokens.delete_many({"user_id": {"$in": ids}}).deleted_count)
# remove the demo-patient reset token + notification created during the UI test
p = db.users.find_one({"email": "patient@careflow.demo"}, {"_id": 0, "user_id": 1})
if p:
    print("demo patient tokens removed:", db.password_reset_tokens.delete_many({"user_id": p["user_id"]}).deleted_count)
    print("demo patient auth notifications removed:",
          db.notifications.delete_many({"user_id": p["user_id"], "kind": "auth"}).deleted_count)
print("login_attempts cleared:", db.login_attempts.delete_many({"identifier": {"$regex": "^203.0.113."}}).deleted_count)

s = requests.Session()
s.headers.update({"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.77"})
for email in ["patient@careflow.demo", "doctor1@careflow.demo", "admin@careflow.demo",
              "doctor2@careflow.demo", "patient1@careflow.demo"]:
    r = s.post(f"{API}/auth/login", json={"email": email, "password": "CareFlow123!"})
    print(f"{email}: {r.status_code}")
    assert r.status_code == 200, r.text
print("remaining test users:", db.users.count_documents({"email": {"$regex": "^(test_|TEST_)"}}))
