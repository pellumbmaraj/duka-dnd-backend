# Metro frontend integration

The API implements the DTOs and routes declared in the Metro frontend's `lib/backend-contract.ts` and `lib/backend-api.ts`. Money is represented as integer euro cents. Dates are ISO strings.

## Local addresses

- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:5000/api/v1`
- Admin panel: `http://127.0.0.1:5000/admin/login`

Set `NEXT_PUBLIC_API_URL=http://localhost:5000` in the frontend. All frontend requests use `credentials: "include"`. The configured `METRO_FRONTEND_ORIGINS` value must exactly match the browser origin.

## Authentication and account

- `GET /auth/csrf`
- `POST /auth/login`
- `POST /auth/complete-activation`
- `GET /auth/me`
- `POST /auth/logout`
- `GET|PATCH /account`
- `PUT /account/password`

Every state changing API request requires the cookie issued by `/auth/csrf`, the exact allowed `Origin`, and its token in `X-CSRF-Token`. Customer passwords and one-time codes use Werkzeug scrypt hashes. An approved applicant signs in with the emailed OTP in the password field, is redirected to `/activate`, and creates the permanent password there. The OTP database record is deleted when that first login succeeds.

## Storefront resources

- `/configuration` and `/storefront/bootstrap`
- `/businesses`, `/businesses/{id}`, and address routes
- `/businesses/applications`
- `/catalog`, `/catalog/categories`, `/catalog/brands`, and product routes
- `/cart` and `/cart/items/{productId}`
- `/saved-lists` and `/saved-lists/{id}`
- `/orders`, `/orders/{id}`, and `/orders/{id}/reorder`
- `/quotes` and `/quotes/{id}`
- `/delivery/options`
- `/comparison`

Catalog data is initially seeded from the current Metro frontend catalog. The server owns prices, VAT, availability, quantity limits, business approval, pricing tier, and order totals. `Idempotency-Key` prevents duplicate order and quote submissions.

The frontend authentication and application pages already call the backend. Its cart and business context providers still contain local storage implementations. Switch those providers to `storefrontApi.bootstrap`, `storefrontApi.cart`, and the other gateway methods to make the UI use these server resources.
