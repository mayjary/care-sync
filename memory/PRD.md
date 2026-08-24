# CareFlow Product Requirements Document

## Original problem statement
Build CareFlow, a production-oriented healthcare coordination platform connecting patients, doctors, and clinic administrators through appointments, doctor availability, notifications, patient records, secure medical reports, role-based access, responsive dashboards, legal pages, and fictional demo data. The first release prioritizes a complete end-to-end core.

## Architecture decisions
- React frontend with React Router, FastAPI backend, and MongoDB using the existing environment configuration.
- JWT email/password sessions in secure httpOnly cookies plus Emergent-managed Google OAuth session exchange.
- Application UUID identifiers and `_id` exclusion before every MongoDB response.
- Private server-side report storage with authenticated, permission-checked streaming.
- Unique MongoDB slot locks plus availability checks to prevent concurrent double booking.
- Database-backed notifications, reminder records, and a background reminder loop.

## Users and core requirements
- Patients: authenticate, find doctors, review availability, manage appointments, notifications, and released health records.
- Doctors: authenticate, view assigned schedule and records, and manage availability.
- Clinic admins: authenticate, target doctor availability, and manage institution-level records through protected APIs.
- All roles receive centralized server-side authorization and fictional demo data.

## Implemented
### 2026-08-24
- Premium landing page, responsive shell, role-aware dashboards, doctor directory, appointments, records, notifications, and legal pages.
- Seeded fictional clinic, five doctors, eight patients, demo patient/doctor/admin accounts, JWT auth, Google callback, logout, health check, and protected APIs.
- Availability-generated slots, atomic slot locks, cancellation handling, patient-visible report permissions, private upload/download streaming, audit-ready record model, and reminder scheduler.
- Patient booking journey from directory: 14-day date calendar, live availability, selectable times, confirmation, backend appointment creation.
- Clinic admin report workspace: private-by-default upload, explicit release to patient, and pending patient-document review (accept/reject).

### 2026-08-24 (QA hardening pass)
- Fixed mobile duplicate `.admin-report-grid` rule; grid now correctly single-column at 375/390px.
- Doctor-scoped report file access; patient-document upload now fans out to all admins.
- Added self-isolated concurrent-booking regression test.

### 2026-08-24 (feature pass — availability, unavailability, patient uploads)
- **Doctor Availability Editor** (`/app/schedule`): weekly view with 7 day cards, add/remove periods per day, per-day slot duration (15/20/30/45/60), mark day unavailable, saves through `PUT /api/availability/{doctor_id}` with server-side validation (HH:MM format, start<end, no overlaps per weekday including across duplicate entries, duration 5–240). Doctors edit their own; admin can edit any doctor via `schedule-doctor-select`. Slot generation reads live weekly rules and returns [] for dates inside an exception range.
- **Doctor Unavailability**: `POST /api/availability/{doctor_id}/exceptions` blocks a date range, flags every affected confirmed appointment as `needs_rescheduling` (original preserved), releases the slot lock, and sends a notification containing doctor name + date + time. `DELETE /api/availability/exceptions/{id}` removes a block. Appointments outside the range are untouched. UI: "Time off" form on `/app/schedule`, appointments in `needs_rescheduling` show a **Rebook** CTA that jumps to `/app/doctors`.
- **Patient Uploads Page** (`/app/uploads`): patients submit past reports (multipart PDF/JPG/PNG), see live status (pending / accepted / rejected) via colored badges, and can View any submitted document via `GET /api/patient-documents/{document_id}/file` (owner-only for patients; admins can view; doctors 403).
- Legacy `POST /api/availability` retained for backwards compatibility, but now validates time format, start<end, duration bounds and always persists the computed `doctor_id`.
- Shell nav highlight switched to `useLocation()` for reliable React Router active state.
- Error vs. success messaging now uses dedicated `.schedule-alert.error` styling; overlap and file errors show red, saves show mint-green.
- Verified: 98/98 backend pytest assertions (31 new for these features + 67 prior) and all UI fixes across 375/390/1280px.

## Prioritized backlog
### P0 — none open

### P1
- End-to-end verification of Google OAuth callback flow against configured provider.
- Report versioning, archival, and richer report viewer (PDF zoom / pagination).
- Optional: per-period duration in `GET /api/availability` and overlap validation in legacy `POST /api/availability` to match `PUT`.
- Optional: cap/paginate the patient "My submissions" list on `/app/uploads`; add a delete endpoint for patient-uploaded documents.

### P2
- Rate limiting, audit-log viewer, report access audit trail, CSRF/session posture review.
- Institution branches/departments, timezone preferences, accessibility audit, and translations.
- Optional platform-level super-admin surface.
- Reformat monolithic `server.py` and `App.js` into modules.

## Next tasks
1. Google OAuth end-to-end verification.
2. Report viewer improvements (PDF zoom / pagination / image fullscreen).
3. Optional legacy `POST /api/availability` deprecation.
