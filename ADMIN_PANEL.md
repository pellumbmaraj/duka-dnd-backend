# DUKA Group admin workspace

A plain HTML, CSS, and JavaScript staff interface served by Flask:

- `GET /admin/login` — DUKA Group branded staff sign-in.
- `GET /admin` — protected overview showing live active-account and staff counts. Catalog and order tiles are marked as not connected until those backend features exist.
- `POST /admin/logout` — CSRF-protected sign-out.

Sign-in accepts only active `staff` and `admin` accounts. On the first `python main.py` startup, the backend automatically creates the temporary `admin@dukagroup.al` administrator account in SQLite. Its password is stored as a scrypt hash. Later starts reuse the saved account and do not prompt in the terminal. Customer accounts cannot open the panel.

The interface uses a DUKA Group identity built with local HTML and CSS, with no D&D branding. It uses local HTML/CSS/JS only and adds a restrictive same-origin Content Security Policy. All admin routes use the same secure, HttpOnly, SameSite session cookie and authentication rate limiter as the login API.

Run `python main.py`, then open `http://127.0.0.1:5000/admin/login`. The Clients page can create approved business accounts, approve pending applications, and generate a one-time password. The Orders page lists orders stored through the frontend API. The Analytics page reports orders and units for the last 30 days, compares them with the preceding period, and groups activity by client, status, and day. Catalog editing, order status controls, password delivery/reset, and audit history have not been implemented yet.
