# CareFlow Auth Testing Playbook

## Overview
CareFlow uses a custom email/password JWT auth flow with an httpOnly cookie session,
bcrypt password hashing (via passlib), MongoDB user store, brute-force lockout,
and password reset tokens with a Mongo TTL index.

## Endpoints (all mounted under /api)
- POST `/api/auth/register` — create a patient/doctor account (min password length 8)
- POST `/api/auth/login` — sets `session_token` httpOnly cookie
- GET `/api/auth/me` — current authenticated user
- POST `/api/auth/logout` — clears cookie
- POST `/api/auth/forgot-password` — issues a reset token (link is logged, in demo also written to the user's in-app notifications)
- POST `/api/auth/reset-password` — consume token and set a new password
- POST `/api/auth/google` — Emergent-managed Google callback (separate integration)

## Configuration
Backend env (from `backend/.env`):
- `MONGO_URL`, `DB_NAME` — Mongo connection (Emergent-managed in preview)
- `AUTH_SECRET` — JWT signing secret (Emergent-managed in preview)
- `EMERGENT_LLM_KEY` — Emergent Universal Key (for object storage; unrelated to auth but part of the same env)

Cookie flags: `httpOnly=True`, `secure=True`, `samesite="none"`, `max_age=7 days`.

## Password rules
- bcrypt via passlib (`$2b$` hashes)
- Minimum 8 characters at register and reset
- Never returned by any endpoint (`public()` strips `password_hash`)

## Brute-force lockout
- Tracked in `login_attempts` collection keyed by `"{ip}:{email_lowercased}"`
- 5 failed logins inside 15 minutes → HTTP 429 "Too many failed sign-in attempts…"
- Successful login clears the record

## Reset tokens
- Stored in `password_reset_tokens`, 1 hour TTL via `expireAfterSeconds=0` index on `expires_at`
- Single-use; consuming the token flips `used=True`

## Mongo indexes created at startup
- `users.email` unique
- `login_attempts.identifier` unique
- `password_reset_tokens.expires_at` TTL
- `password_reset_tokens.token` unique
- `slot_locks (doctor_id, appointment_date, appointment_time)` unique
- `reminders (appointment_id, lead_hours)` unique

## Seeded demo credentials (see `/app/memory/test_credentials.md`)
All demo accounts share password `CareFlow123!`.
- Patient: `patient@careflow.demo`
- Doctor: `doctor1@careflow.demo` … `doctor5@careflow.demo`
- Clinic admin: `admin@careflow.demo`
- Additional patients: `patient1@careflow.demo` … `patient8@careflow.demo`

## Sample cURL flows
```bash
API=$REACT_APP_BACKEND_URL/api

# Login and capture cookie
curl -s -c /tmp/c.txt -X POST "$API/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"patient@careflow.demo","password":"CareFlow123!"}'
curl -s -b /tmp/c.txt "$API/auth/me"

# Forgot / reset (email link is only logged in demo — grab the token from backend logs
# or /api/notifications for the target user)
curl -s -X POST "$API/auth/forgot-password" -H "Content-Type: application/json" \
  -d '{"email":"patient@careflow.demo"}'
curl -s -X POST "$API/auth/reset-password" -H "Content-Type: application/json" \
  -d '{"token":"<paste-from-log>","password":"NewStrongPass1"}'

# Brute force (6 wrong tries triggers 429)
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST "$API/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"email":"patient@careflow.demo","password":"wrong"}'
done
```

## Frontend routes and data-testids
- `/login`: `login-{patient|doctor|admin}-tab`, `login-email-input`, `login-password-input`,
  `login-submit-button`, `authentication-error`, `forgot-password-link`, `google-login-button`.
- `/forgot-password`: `forgot-password-form`, `forgot-email-input`, `forgot-message`,
  `forgot-submit-button`, `back-to-login-link`.
- `/reset-password?token=...`: `reset-password-form`, `reset-password-input`,
  `reset-password-confirm`, `reset-message`, `reset-submit-button`, `back-to-login-link`,
  `reset-missing-token` (shown when the URL has no token).
