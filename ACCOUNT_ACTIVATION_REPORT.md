# DUKA Group Account Request and Activation Report

**Date:** 24 September 2026  
**Backend:** Flask and SQLite  
**Frontend:** Metro Next.js storefront

## Purpose

This report documents the implemented business account request, administrative verification, email OTP, and first-password workflow connecting the Metro frontend with the DUKA Group administration panel.

## Implemented workflow

1. A prospective client submits the business account form from the Metro frontend.
2. The backend validates the submitted company name, NIPT, contact name, business email, phone number, and address.
3. The request is stored as a business application. It does not create a customer account immediately.
4. The DUKA Group admin dashboard displays the number of unread account requests.
5. Opening a request displays the complete submitted information and records that the request was viewed.
6. Selecting **Continue to verification** moves the request into the verification queue.
7. The Clients section opens with every submitted field automatically filled into the verification form.
8. The request remains visible in the dedicated **Waiting for verification** section until an administrator makes a decision.
9. Selecting **Verify, accept and email OTP** creates the approved business, its primary address, customer user, and business membership.
10. An eight-digit, one-time login code is emailed to the submitted business email address.
11. The client signs in through the normal Metro login page using the business email and OTP in the password field.
12. After successful OTP login, the OTP record is deleted and the client is redirected to `/activate`.
13. The activation session is restricted from storefront resources until the client creates a permanent password.
14. The client enters and confirms a new password. The password is stored as a scrypt hash and the account becomes fully usable.

## Admin panel changes

The admin panel now contains:

- A dashboard notification for unread account requests.
- Live five-second notification polling, counter updates, and clickable in-panel toasts without a page refresh.
- An **Account requests** inbox.
- Full application detail pages.
- Read-state tracking when a request is opened.
- Continue-to-verification and rejection actions.
- A dedicated waiting-for-verification section in Clients.
- An automatically populated verification form.
- Final verification, account creation, and activation-email delivery.
- OTP resend support for accounts that have not created a permanent password.
- Existing client status and price-tier controls.

Final approval remains in the verification queue when email delivery fails. This prevents the interface from claiming that an activation email was sent when it was not delivered through SMTP.

## Frontend changes

The Metro frontend now:

- Submits business applications through the Flask API.
- Detects `mustChangePassword` after OTP login.
- Redirects OTP-authenticated clients to `/activate`.
- Provides a first-password form with confirmation and client-side validation.
- Calls the protected `/api/v1/auth/complete-activation` endpoint.
- Redirects the client to the storefront after successful password creation.
- Includes `/activate` in the frontend security proxy configuration.

## Database changes

### `business_applications`

Stores the original request independently from approved clients, including:

- Submitted business and contact information.
- Workflow status: `new`, `verification`, `approved`, or `rejected`.
- Opened, verification-started, and decision timestamps.
- Administrators responsible for each workflow step.
- The resulting business ID after approval.

Partial unique indexes prevent simultaneous open requests with the same business email or NIPT.

### `account_activation_otps`

Stores:

- Customer user ID.
- Scrypt OTP hash.
- Failed-attempt count.
- Expiration time.
- Creation time.

The plaintext OTP is never stored in the database.

## OTP and password security

- OTPs contain eight cryptographically generated digits.
- OTPs expire after 30 minutes by default.
- Verification is limited to five failed attempts.
- Expired and exhausted OTP records are deleted.
- A successfully used OTP is deleted immediately after the first login.
- Login rate limiting also applies to OTP attempts.
- OTP authentication creates a restricted, signed, HttpOnly session.
- Storefront API access remains blocked until password creation.
- Permanent passwords require 12 to 256 characters, at least one letter, and at least one number.
- Permanent passwords use Werkzeug's scrypt password hashing.
- CSRF verification and exact allowed-origin checks protect activation requests.

## Email configuration

The backend sends activation messages through SMTP. Add the following values to `.env`:

```env
FRONTEND_PUBLIC_URL=http://localhost:3000

SMTP_HOST=smtp.your-provider.com
SMTP_PORT=587
SMTP_USERNAME=your-smtp-username
SMTP_PASSWORD=your-smtp-password
SMTP_USE_TLS=true
SMTP_USE_SSL=false

MAIL_FROM=accounts@dukagroup.al
MAIL_FROM_NAME=DUKA Group
ACTIVATION_OTP_MINUTES=30
```

Use either TLS or SSL according to the email provider's requirements. `FRONTEND_PUBLIC_URL` is used for the login link included in activation messages.

## Main endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/businesses/applications` | Submit a business account request |
| `POST` | `/api/v1/auth/login` | Sign in with a permanent password or valid OTP |
| `POST` | `/api/v1/auth/complete-activation` | Store the client's first permanent password |
| `GET` | `/admin/applications` | List account requests |
| `GET` | `/admin/applications/{id}` | Open the full request |
| `POST` | `/admin/applications/{id}/continue` | Move the request to verification |
| `POST` | `/admin/applications/{id}/verify` | Approve, create the account, and email the OTP |
| `POST` | `/admin/applications/{id}/reject` | Reject the request |
| `POST` | `/admin/clients/{id}/resend-activation` | Issue and email a replacement OTP |

## Important files

### Backend

- `backend/admin.py` — application review, verification, approval, and resend actions.
- `backend/auth.py` — OTP login, deletion, restricted activation session, and permanent-password creation.
- `backend/database.py` — application and activation database schema.
- `backend/mailer.py` — SMTP activation email delivery.
- `backend/templates/admin/applications.html` — account request inbox.
- `backend/templates/admin/application_detail.html` — complete application view.
- `backend/templates/admin/clients.html` — verification queue and auto-filled approval form.
- `backend/templates/admin/dashboard.html` — new-request notification.

### Frontend

- `lib/backend-api.ts` — activation API client.
- `app/login/login-form.tsx` — OTP login redirect handling.
- `app/activate/page.tsx` — activation page.
- `app/activate/activation-form.tsx` — permanent-password form.
- `proxy.ts` — activation route security policy.

## Verification performed

The implemented flow was exercised against an isolated database with email sending captured by the application's test outbox:

- Business application submission returned `201`.
- Admin sign-in succeeded.
- Full application details loaded successfully.
- Continue-to-verification redirected to the populated Clients form.
- Final verification created the business, address, customer, and membership.
- The activation email contained the generated OTP.
- OTP login succeeded and returned `mustChangePassword: true`.
- The OTP table was empty immediately after successful OTP login.
- Storefront bootstrap returned `403 password_setup_required` before password creation.
- Permanent-password creation succeeded.
- Storefront bootstrap returned `200` afterward.
- The application was recorded as approved and linked to the created business.
- The migrated project database passed SQLite `PRAGMA integrity_check`.
- Backend Python compilation and patch whitespace checks passed.

## Running the application

```bash
cd /home/pume-laptop/Desktop/duka-project-1/backend/duka-dnd-backend
source venv/bin/activate
python main.py
```

Open the admin panel at `http://127.0.0.1:5000/admin/login` and the Metro frontend at `http://localhost:3000`.

## Operational requirement

A valid SMTP account is required before real activation emails can be delivered. The application intentionally blocks final approval when `SMTP_HOST` is missing and reports failed SMTP delivery to the administrator.

The host machine has no configured SMTP values and no local `sendmail`, `mail`, or `msmtp` transport. A provider host and credential must therefore be supplied before external email delivery can work.
