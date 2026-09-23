# Login API (initial backend implementation)

The Metro frontend currently displays a demo sign-in and blocks account-service POST requests. This backend is ready for a later frontend integration; it does not change that UI.

- `GET /api/v1/auth/csrf` — from an allowed frontend origin, establishes a short-lived pre-auth session and returns a CSRF token.
- `POST /api/v1/auth/login` — JSON `{ "email": "…", "password": "…" }`; requires the allowed `Origin`, the pre-auth session cookie, and `X-CSRF-Token`. On success it clears pre-auth state and issues an authenticated Flask signed, HttpOnly cookie session. The cookie is integrity-protected, not encrypted or stored server-side; it contains only account identifiers and an auth version.
- `GET /api/v1/auth/me` — returns the current account after checking its active status and auth version.
- `POST /api/v1/auth/logout` — requires the allowed `Origin` and `X-CSRF-Token`, then clears the session.

On the first `python main.py` startup, the backend automatically stores one temporary administrator in SQLite. There is no public registration or password reset. The frontend login integration is not included yet. Store a strong `FLASK_SECRET_KEY` outside source control, set `METRO_ENV=production` only when serving over HTTPS, set exact frontend origins, use a production WSGI server, and back up/restrict the database file before deployment. For production deployments with multiple app workers, move rate-limit state from SQLite to a shared atomic store and add monitoring / edge rate limits.
