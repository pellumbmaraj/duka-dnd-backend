# Metro Flask backend

This project is a working Flask and SQLite backend for the Metro wholesale storefront and DUKA Group administration portal.

## Main modules

- `main.py` loads local environment settings, creates the app, seeds the first administrator, and starts Flask.
- `backend/auth.py` contains browser session authentication, scrypt password checking, CSRF checks, and sign in throttling.
- `backend/commerce.py` implements the versioned storefront JSON API.
- `backend/database.py` creates and migrates the SQLite schema and seeds the storefront catalog.
- `backend/admin.py` implements the staff-only HTML admin routes.
- `backend/templates/admin` and `backend/static/admin` contain the plain HTML, CSS, and JavaScript panel.
- `backend/catalog_seed.json` contains the initial catalog imported from the current Metro frontend.

## Stored domains

The database stores users, business applications and their review state, single-use activation hashes, memberships, addresses, catalog categories and brands, products, volume pricing, carts, saved lists, comparisons, orders, quotes, delivery options, and authentication rate-limit buckets.

Commercial calculations run on the server. Submitted client totals are ignored. Order and quote lines preserve price snapshots, while storefront products continue to use the current catalog price.
