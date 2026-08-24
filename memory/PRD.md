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

### 2026-08-24 (final QA pass)
- Fixed mobile responsive bug: duplicate `.admin-report-grid` rule after the `@media(max-width:850px)` override was removed from `App.css`. Verified computed `grid-template-columns` is a single track at 375/390px and two tracks at 1200px; review queue stacks below the upload panel; no horizontal overflow.
- Restricted `GET /api/reports/{id}/file` for doctors to reports they uploaded (was previously unrestricted for the doctor role).
- Replaced hard-coded `demo_admin` notification target in patient-document upload with a fan-out to every user with `role=admin`.
- Added self-isolated concurrent-booking regression test (`test_concurrent_booking_fresh_slot_isolated`) that always picks a fresh future date/slot so `[409,409]` from stale fixtures cannot recur.
- Testing-agent iteration 5: 18/18 backend assertions pass, admin/patient UI flows pass, responsive grid pass.

## Prioritized backlog
### P0 — none open

### P1
- Doctor unavailability workflow: mark blocks, identify affected appointments, notify patients, and offer alternate slots.
- Doctor availability editor UI and appointment status management (reschedule/cancel/no-show) from doctor and admin dashboards.
- Patient-facing UI to submit personal documents and see the accepted/rejected status (endpoint already implemented).
- End-to-end verification of Google OAuth callback flow against configured provider.
- Report versioning, archival, and richer report viewer (PDF zoom / pagination).

### P2
- Rate limiting, audit-log viewer, report access audit trail, CSRF/session posture review.
- Institution branches/departments, pagination, timezone preferences, accessibility audit, and translations.
- Optional platform-level super-admin surface.

## Next tasks
1. Doctor unavailability + reschedule workflow.
2. Patient personal document upload UI.
3. Doctor availability editor + admin appointment status controls.
