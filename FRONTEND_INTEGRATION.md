# Metro frontend integration foundation

The Flask backend now has the first database and API boundaries needed by the Metro frontend. The frontend has not been modified yet.

## Authentication flow

1. Request `GET http://127.0.0.1:5000/api/v1/auth/csrf` with `credentials: "include"`.
2. Send `POST /api/v1/auth/login` with JSON email/password, `credentials: "include"`, and the returned token in `X-CSRF-Token`.
3. Retain the new CSRF token returned by a successful login for state-changing requests.
4. Request `GET /api/v1/auth/me` to restore the authenticated account.

The backend allows the configured `METRO_FRONTEND_ORIGINS` value, which defaults to `http://localhost:3000`. Browser requests must include credentials so the HttpOnly session cookie is sent.

## Business onboarding

`POST /api/v1/businesses/applications` accepts a CSRF-protected JSON object containing `companyName`, `taxId`, `contactName`, `email`, `phone`, and `address`. It creates a pending business application without creating credentials.

An administrator can also create an already approved client at `/admin/clients`. Approving an application or creating a client generates a one-time password, stores only its scrypt hash, and displays the password once to the administrator.

## Orders

`POST /api/v1/orders` accepts an approved, authenticated business client's order:

```json
{
  "address": "Delivery address",
  "note": "Optional note",
  "items": [
    { "productId": "mock-divella-1", "qty": 4 }
  ]
}
```

Use a unique `Idempotency-Key` request header to prevent a repeated checkout request from creating a duplicate order. `GET /api/v1/orders` returns the signed-in business's latest orders.

The server does not trust or store frontend totals as authoritative. Product pricing and tax must be moved into the backend catalog before order totals can be calculated and orders can advance beyond initial submission.
