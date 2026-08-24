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
- Built premium CareFlow landing page, responsive shell, role-aware dashboards, doctor directory, appointments, records, notifications, and legal pages.
- Added seeded fictional clinic, five doctors, eight patients, demo patient/doctor/admin accounts, JWT auth, Google callback, logout, health check, and protected APIs.
- Added availability-generated slots, atomic slot locks, cancellation handling, patient-visible report permissions, private upload/download streaming, audit-ready record model, and reminder scheduler.
- Verified with lint, production build, external API checks, responsive screenshot, and iteration-2 end-to-end QA.
- Added the patient booking journey from the doctor directory: 14-day date calendar, live availability requests, selectable times, confirmation messaging, and backend appointment creation.
- Verified the booking journey through browser QA on desktop and mobile, including dialog close, live slot selection, confirmation, and no horizontal overflow.

## Prioritized backlog
### P0
- Expand admin UI for report upload/release, patient document review, doctor management, and appointment status controls.
- Add explicit rescheduling and doctor-unavailability exception screens around the existing protected APIs.

### P1
- Add full patient registration/profile/settings forms and doctor availability editor UI.
- Add audit log viewer, report metadata/version management, and operational analytics.
- Add email provider adapter controlled entirely by environment configuration.

### P2
- Add richer institution branches/departments, pagination, timezone preferences, accessibility audit, and translations.

## Next tasks
1. Build the clinic admin report release and patient-document review screens.
2. Add patient booking date/slot picker connected to `/api/slots` and `/api/appointments`.
3. Add doctor unavailable date ranges with affected-appointment rescheduling workflow.