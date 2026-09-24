# DUKA Group admin panel

Run `python main.py`, then open `http://127.0.0.1:5000/admin/login`.

The first run creates this temporary administrator when no admin account exists:

- Email: `admin@dukagroup.al`
- Password: `DukaGroupAdmin2026!`

The panel uses plain server rendered HTML, CSS, and JavaScript with DUKA Group styling. Active `staff` and `admin` accounts can open it. Administrative changes are protected by the same HttpOnly session, role checks, CSRF tokens, and sign in rate limit as the API.

Available sections:

- Overview: account, client, and recent order totals.
- Account requests: an inbox for new frontend applications, full submitted details, read state, rejection, and transfer to verification.
- Clients: an auto-filled verification form, a waiting-for-verification queue, final approval, activation-code resend, account status, and price tier controls.
- Orders: list orders, inspect server priced lines, and update fulfillment status.
- Quotes: list requests, inspect lines, and update quote status.
- Catalog: change price, available units, availability, and storefront visibility.
- Analytics: 30 day order counts, units, comparison to the previous period, daily activity, client activity, and status totals.

Replace the temporary admin password before any real deployment.

## Client activation email

Final application approval sends an eight-digit, single-use OTP to the submitted business email. The hashed OTP expires after 30 minutes by default and is deleted at successful OTP login. That login can only access the first-password endpoint until the client creates a permanent password.

Configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS` or `SMTP_USE_SSL`, `MAIL_FROM`, and `FRONTEND_PUBLIC_URL` in `.env`. Approval remains in the verification queue if the activation message cannot be delivered.
