# Metro Flask backend structure

This is a structure plan for the Metro (D&D Distribution) wholesale storefront. It is intentionally scaffolding only: no API, database, authentication, admin screens, or business logic has been implemented.

## Planned layout

- `main.py`, `backend/` — existing Flask starter entry point and app factory modules; retained unchanged.
- `app/api/v1/` — versioned JSON API blueprints, grouped by auth, catalog, companies, customers, saved lists, quotes, orders, and delivery.
- `app/models/` — planned database entities and relationships.
- `app/repositories/` — planned database access layer.
- `app/services/` — planned pricing, validation, quote/order workflows, and other business rules.
- `app/extensions/` — planned shared Flask extensions (database, migrations, login/session, and admin integration).
- `app/config/` — planned development, test, and production settings.
- `app/admin/` — planned staff-only admin interface, with view, template, and static asset areas.
- `migrations/` — planned database schema migration history.
- `tests/` — planned automated test organization.

## Feature mapping from the frontend

- Catalog: products, localized English/Albanian names, categories, brands, company, images, SKU, availability, pack sizes, and badges.
- Authentication and customers: real account registration/sign-in, customer company/contact/tax details, roles, delivery addresses, and sessions. The existing frontend login is a demo and grants no authorization.
- Pricing: server-owned base prices, VAT configuration, account pricing tiers, offers, case/minimum quantities, and volume discounts. The frontend's trade tier and all displayed commercial/specification data are demo values; never trust client-supplied prices, totals, tax, or role.
- Lists and cart: persist customer shopping lists and validate submitted product quantities against current catalog rules.
- Quotes and orders: store quote/order records and line-item price snapshots. Checkout and quote submission are currently local demos and do not transmit records.
- Delivery: maintain service areas, schedules, and cutoff settings; the displayed values are illustrative.
- Admin: staff authentication/authorization and management of products, brands, categories, companies, customer accounts, price rules, offers, orders, quotes, and delivery settings.

## Suggested admin boundary

Keep the staff panel separate from customer API routes, require staff roles on every admin action, and record sensitive changes in an audit log. Flask-Admin (or an equivalent Flask admin extension) can provide the panel once implementation begins. Do not make the frontend's display-only demo identity an authorization credential.

## Frontend integration notes

The current frontend catalog is in `frontend/metro/lib/data.ts`; commerce rules are in `frontend/metro/lib/commerce.ts`; business profile, lists, quote/order history, and comparison selections are browser-persisted in `frontend/metro/lib/business-context.tsx`. The backend should replace those browser-only records with authenticated API persistence after product data and commercial terms have been verified by D&D.
