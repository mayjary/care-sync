# CareFlow Authentication Testing Playbook

## Demo authentication checks

1. Verify seeded patient, doctor, and clinic-admin accounts can log in through `/api/auth/login`.
2. Verify `/api/auth/me` returns the authenticated user and role.
3. Verify an invalid password returns a safe 401 response.
4. Verify logout invalidates the session and clears the cookie.
5. Verify protected routes reject missing or expired sessions.
6. Verify patient, doctor, and clinic-admin route permissions are enforced server-side.

## Google OAuth checks

1. Confirm the login link derives its redirect from `window.location.origin`.
2. Confirm the callback reads `useLocation().hash`, exchanges `session_id` server-side, and redirects to the role dashboard.
3. Confirm the persistent session is stored in an httpOnly cookie and `/api/auth/me` remains the source of truth.

## MongoDB safety checks

- User records use an application `user_id` UUID.
- Session `user_id` matches the user record.
- API responses exclude MongoDB `_id`.
- No password or secret is returned to the browser.