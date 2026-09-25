import hmac
import re
import secrets
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import _DUMMY_HASH, _rate_limited
from .database import get_db
from .mailer import send_activation_email

admin = Blueprint(
    "admin",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/admin/static",
)

TEMPORARY_ADMIN_EMAIL = "admin@dukagroup.al"
TEMPORARY_ADMIN_PASSWORD = "DukaGroupAdmin2026!"
ORDER_STATUSES = ("submitted", "confirmed", "processing", "shipped", "completed", "cancelled")
QUOTE_STATUSES = ("submitted", "reviewing", "accepted", "rejected", "expired")
CLIENT_STATUSES = ("pending", "approved", "rejected", "suspended")
ALBANIAN_LABELS = {
    "submitted": "Dërguar", "confirmed": "Konfirmuar", "processing": "Në përpunim",
    "shipped": "Në transport", "completed": "Përfunduar", "cancelled": "Anuluar",
    "pending": "Në pritje", "approved": "Miratuar", "rejected": "Refuzuar",
    "suspended": "Pezulluar", "verification": "Në verifikim", "new": "E re",
    "reviewing": "Në shqyrtim", "accepted": "Pranuar", "expired": "Skaduar",
    "standard": "Standard", "trade": "Tregtar", "admin": "Administrator",
    "staff": "Staf", "customer": "Klient", "cod": "Pagesë në dorëzim",
    "pickup": "Pagesë në marrje", "invoice": "Faturë bankare",
}


@admin.app_template_filter("label_sq")
def label_sq(value):
    return ALBANIAN_LABELS.get(str(value), str(value))


def bootstrap_first_admin():
    """Create the initial admin and configured operational staff accounts."""
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        existing = db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if not existing:
            db.execute(
                "INSERT INTO users(email, password_hash, display_name, role) VALUES (?, ?, ?, 'admin')",
                (
                    TEMPORARY_ADMIN_EMAIL,
                    generate_password_hash(TEMPORARY_ADMIN_PASSWORD, method="scrypt"),
                    "Administratori DUKA",
                ),
            )
        else:
            db.execute("UPDATE users SET display_name = ? WHERE id = ?", ("Administratori DUKA", existing["id"]))
        operational_accounts = (
            ("packing", current_app.config.get("PACKING_STAFF_EMAIL"), current_app.config.get("PACKING_STAFF_PASSWORD"), "Operatori i Paketimit"),
            ("delivery", current_app.config.get("DELIVERY_STAFF_EMAIL"), current_app.config.get("DELIVERY_STAFF_PASSWORD"), "Operatori i Dorëzimit"),
        )
        for operational_role, email, password, display_name in operational_accounts:
            if not email or not password:
                continue
            email = email.strip().casefold()
            account = db.execute("SELECT id, role FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
            if account and account["role"] != "staff":
                raise RuntimeError(f"{operational_role.title()} staff email is already used by another account role.")
            if not account:
                cursor = db.execute(
                    "INSERT INTO users(email,password_hash,display_name,role) VALUES (?,?,?,'staff')",
                    (email, generate_password_hash(password, method="scrypt"), display_name),
                )
                user_id = cursor.lastrowid
            else:
                user_id = account["id"]
                db.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id))
            db.execute(
                "INSERT INTO staff_profiles(user_id,operational_role) VALUES (?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET operational_role=excluded.operational_role",
                (user_id, operational_role),
            )
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def _csrf_valid(field_name="csrf_token"):
    submitted = request.form.get(field_name, "")
    expected = session.get("admin_csrf_token", "")
    return bool(submitted and expected and hmac.compare_digest(submitted, expected))


def _current_admin():
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = get_db().execute(
        "SELECT id, email, display_name, role, is_active, auth_version "
        "FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if (
        not user
        or not user["is_active"]
        or user["role"] not in {"staff", "admin"}
        or user["auth_version"] != session.get("auth_version")
    ):
        session.clear()
        return None
    session.setdefault("admin_csrf_token", secrets.token_urlsafe(32))
    return user


def _operation_role(user_id):
    row = get_db().execute(
        "SELECT operational_role FROM staff_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row["operational_role"] if row else None


def _home_for_user(user):
    return url_for("admin.operations") if _operation_role(user["id"]) else url_for("admin.dashboard")


def _admin_or_redirect():
    user = _current_admin()
    if not user:
        return None, redirect(url_for("admin.login_page"))
    if _operation_role(user["id"]):
        return None, redirect(url_for("admin.operations"))
    return user, None


def _operation_or_redirect():
    user = _current_admin()
    if not user:
        return None, None, redirect(url_for("admin.login_page"))
    role = _operation_role(user["id"])
    if not role:
        return None, None, redirect(url_for("admin.dashboard"))
    return user, role, None


def _client_page(user, new_credentials=None, application=None):
    if application is None:
        application_id = request.args.get("application", type=int)
        if application_id:
            application = get_db().execute(
                "SELECT * FROM business_applications WHERE id = ? AND status = 'verification'",
                (application_id,),
            ).fetchone()
    clients = get_db().execute(
        "SELECT b.*, u.id AS user_id, u.must_change_password FROM business_clients b "
        "LEFT JOIN business_members m ON m.business_id = b.id "
        "LEFT JOIN users u ON u.id = m.user_id "
        "ORDER BY CASE b.status WHEN 'pending' THEN 0 ELSE 1 END, b.id DESC"
    ).fetchall()
    waiting_applications = get_db().execute(
        "SELECT * FROM business_applications WHERE status = 'verification' ORDER BY created_at"
    ).fetchall()
    return render_template(
        "admin/clients.html",
        user=user,
        clients=clients,
        csrf_token=session["admin_csrf_token"],
        new_credentials=new_credentials,
        application=application,
        waiting_applications=waiting_applications,
    )


def _valid_client_form():
    values = {
        "company_name": request.form.get("company_name", "").strip(),
        "tax_id": request.form.get("tax_id", "").strip(),
        "contact_name": request.form.get("contact_name", "").strip(),
        "email": request.form.get("email", "").strip().casefold(),
        "phone": request.form.get("phone", "").strip(),
        "address": request.form.get("address", "").strip(),
    }
    lengths = {"company_name": 200, "tax_id": 50, "contact_name": 100, "email": 254, "phone": 50, "address": 500}
    required = ("company_name", "tax_id", "contact_name", "email")
    if any(not values[key] for key in required) or any(len(values[key]) > limit for key, limit in lengths.items()):
        return None
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", values["email"]):
        return None
    return values


@admin.get("/admin/login")
def login_page():
    user = _current_admin()
    if user:
        return redirect(_home_for_user(user))
    session["admin_csrf_token"] = secrets.token_urlsafe(32)
    return render_template("admin/login.html")


@admin.post("/admin/login")
def login_submit():
    if not _csrf_valid():
        abort(400)
    email = request.form.get("email", "").strip().casefold()
    password = request.form.get("password", "")
    if len(email) > 254 or len(password) > 256 or not email or not password:
        flash("Emaili ose fjalëkalimi është i pasaktë.", "error")
        return redirect(url_for("admin.login_page"))

    if _rate_limited(email):
        flash("Ka shumë tentativa hyrjeje. Provoni përsëri pas pak.", "error")
        return render_template("admin/login.html"), 429

    user = get_db().execute(
        "SELECT id, email, password_hash, display_name, role, is_active, auth_version "
        "FROM users WHERE email = ? COLLATE NOCASE",
        (email,),
    ).fetchone()
    matches = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
    if not user or not matches or not user["is_active"] or user["role"] not in {"staff", "admin"}:
        flash("Emaili ose fjalëkalimi është i pasaktë.", "error")
        return redirect(url_for("admin.login_page")), 401

    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["auth_version"] = user["auth_version"]
    session["admin_csrf_token"] = secrets.token_urlsafe(32)
    return redirect(_home_for_user(user))


@admin.get("/admin")
def dashboard():
    user = _current_admin()
    if not user:
        return redirect(url_for("admin.login_page"))
    if _operation_role(user["id"]):
        return redirect(url_for("admin.operations"))
    db = get_db()
    account_count = db.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
    staff_count = db.execute(
        "SELECT COUNT(*) FROM users WHERE is_active = 1 AND role IN ('staff', 'admin')"
    ).fetchone()[0]
    pending_clients = db.execute("SELECT COUNT(*) FROM business_clients WHERE status = 'pending'").fetchone()[0]
    new_applications = db.execute(
        "SELECT COUNT(*) FROM business_applications WHERE status = 'new' AND opened_at IS NULL"
    ).fetchone()[0]
    verification_applications = db.execute("SELECT COUNT(*) FROM business_applications WHERE status = 'verification'").fetchone()[0]
    order_count = db.execute(
        "SELECT COUNT(*) FROM orders WHERE created_at >= datetime('now', '-30 days')"
    ).fetchone()[0]
    order_alert_count = db.execute(
        "SELECT COUNT(*) FROM orders WHERE status = 'submitted'"
    ).fetchone()[0]
    latest_order_id = db.execute("SELECT COALESCE(MAX(id), 0) FROM orders").fetchone()[0]
    latest_order_event_id = db.execute("SELECT COALESCE(MAX(id), 0) FROM order_status_events").fetchone()[0]
    latest_notifications = db.execute("SELECT * FROM admin_notifications ORDER BY id DESC LIMIT 8").fetchall()
    latest_notification_id = db.execute("SELECT COALESCE(MAX(id),0) FROM admin_notifications").fetchone()[0]
    return render_template(
        "admin/dashboard.html",
        user=user,
        account_count=account_count,
        staff_count=staff_count,
        pending_clients=pending_clients,
        new_applications=new_applications,
        verification_applications=verification_applications,
        order_count=order_count,
        order_alert_count=order_alert_count,
        latest_order_id=latest_order_id,
        latest_order_event_id=latest_order_event_id,
        latest_notifications=latest_notifications,
        latest_notification_id=latest_notification_id,
        csrf_token=session["admin_csrf_token"],
    )


@admin.get("/admin/notifications/live")
def admin_notifications():
    user, response = _admin_or_redirect()
    if response:
        return jsonify(error="Kërkohet hyrja në llogari."), 401
    after = max(0, request.args.get("after", 0, type=int))
    db = get_db()
    latest_id = db.execute("SELECT COALESCE(MAX(id),0) FROM admin_notifications").fetchone()[0]
    rows = db.execute(
        "SELECT id,event_type,title,message,target_url,email_status,created_at FROM admin_notifications "
        "WHERE id>? ORDER BY id ASC LIMIT 100", (after,),
    ).fetchall()
    return jsonify(
        latestId=latest_id,
        notifications=[{
            "id": row["id"], "eventType": row["event_type"], "title": row["title"],
            "message": row["message"], "url": row["target_url"],
            "emailStatus": row["email_status"], "createdAt": row["created_at"],
        } for row in rows],
    )


@admin.get("/admin/applications")
def applications():
    user, response = _admin_or_redirect()
    if response:
        return response
    rows = get_db().execute(
        "SELECT * FROM business_applications ORDER BY "
        "CASE status WHEN 'new' THEN 0 WHEN 'verification' THEN 1 ELSE 2 END, id DESC LIMIT 300"
    ).fetchall()
    return render_template(
        "admin/applications.html", user=user, applications=rows,
        csrf_token=session["admin_csrf_token"],
    )


@admin.get("/admin/applications/live")
def application_notifications():
    user, response = _admin_or_redirect()
    if response:
        return jsonify(error="Kërkohet hyrja në llogari."), 401
    db = get_db()
    unread_count = db.execute(
        "SELECT COUNT(*) FROM business_applications WHERE status = 'new' AND opened_at IS NULL"
    ).fetchone()[0]
    verification_count = db.execute(
        "SELECT COUNT(*) FROM business_applications WHERE status = 'verification'"
    ).fetchone()[0]
    latest = db.execute(
        "SELECT id, company_name, created_at FROM business_applications "
        "WHERE status = 'new' AND opened_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return jsonify(
        unreadCount=unread_count,
        verificationCount=verification_count,
        latest={
            "id": latest["id"], "companyName": latest["company_name"], "createdAt": latest["created_at"]
        } if latest else None,
    )


@admin.get("/admin/businesses/live")
def additional_business_notifications():
    user, response = _admin_or_redirect()
    if response:
        return jsonify(error="Kërkohet hyrja në llogari."), 401
    after = max(0, request.args.get("after", 0, type=int))
    db = get_db()
    owner_join = (
        " FROM business_clients b JOIN business_members m ON m.business_id=b.id AND m.member_role='owner' "
        "JOIN users u ON u.id=m.user_id WHERE u.must_change_password=0 "
        "AND (SELECT COUNT(*) FROM business_members owned WHERE owned.user_id=u.id)>1"
    )
    pending_count = db.execute("SELECT COUNT(*) FROM business_clients WHERE status='pending'").fetchone()[0]
    latest_id = db.execute("SELECT COALESCE(MAX(b.id),0)" + owner_join).fetchone()[0]
    rows = db.execute(
        "SELECT b.id,b.company_name,b.status,b.created_at,u.display_name AS owner_name" + owner_join +
        " AND b.id>? ORDER BY b.id ASC LIMIT 100", (after,),
    ).fetchall()
    return jsonify(
        pendingCount=pending_count,
        latestId=latest_id,
        businesses=[{
            "id": row["id"], "companyName": row["company_name"], "ownerName": row["owner_name"],
            "status": row["status"], "createdAt": row["created_at"],
            "url": url_for("admin.clients") + f"#client-{row['id']}",
        } for row in rows],
    )


@admin.get("/admin/orders/live")
def order_notifications():
    user, response = _admin_or_redirect()
    if response:
        return jsonify(error="Kërkohet hyrja në llogari."), 401
    after = max(0, request.args.get("after", 0, type=int))
    after_event = max(0, request.args.get("afterEvent", 0, type=int))
    db = get_db()
    last_30_days = db.execute(
        "SELECT COUNT(*) FROM orders WHERE created_at >= datetime('now', '-30 days')"
    ).fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    alert_count = db.execute(
        "SELECT COUNT(*) FROM orders WHERE status = 'submitted'"
    ).fetchone()[0]
    latest_id = db.execute("SELECT COALESCE(MAX(id), 0) FROM orders").fetchone()[0]
    rows = db.execute(
        "SELECT o.id, o.reference, o.status, o.delivery_address, o.created_at, "
        "o.total_cents, o.currency, b.company_name, COUNT(i.id) AS item_count "
        "FROM orders o JOIN business_clients b ON b.id = o.business_id "
        "LEFT JOIN order_items i ON i.order_id = o.id WHERE o.id > ? "
        "GROUP BY o.id ORDER BY o.id ASC LIMIT 100",
        (after,),
    ).fetchall()
    events = db.execute(
        "SELECT e.id,e.order_id,e.status,e.created_at,o.reference FROM order_status_events e "
        "JOIN orders o ON o.id=e.order_id WHERE e.id>? ORDER BY e.id ASC LIMIT 100", (after_event,),
    ).fetchall()
    latest_event_id = db.execute("SELECT COALESCE(MAX(id),0) FROM order_status_events").fetchone()[0]
    next_event_id = events[-1]["id"] if events else after_event
    return jsonify(
        last30Days=last_30_days,
        total=total,
        alertCount=alert_count,
        latestId=latest_id,
        latestEventId=latest_event_id,
        nextEventId=next_event_id,
        hasMoreEvents=next_event_id < latest_event_id,
        changes=[{"eventId":event["id"],"orderId":event["order_id"],"reference":event["reference"],"status":event["status"],"changedAt":event["created_at"],"url":url_for("admin.order_detail",order_id=event["order_id"])} for event in events],
        orders=[
            {
                "id": row["id"],
                "reference": row["reference"],
                "status": row["status"],
                "deliveryAddress": row["delivery_address"],
                "createdAt": row["created_at"],
                "totalCents": row["total_cents"] or 0,
                "currency": row["currency"],
                "companyName": row["company_name"],
                "itemCount": row["item_count"],
                "url": url_for("admin.order_detail", order_id=row["id"]),
            }
            for row in rows
        ],
    )


@admin.get("/admin/applications/<int:application_id>")
def application_detail(application_id):
    user, response = _admin_or_redirect()
    if response:
        return response
    db = get_db()
    application = db.execute(
        "SELECT * FROM business_applications WHERE id = ?", (application_id,)
    ).fetchone()
    if not application:
        abort(404)
    if not application["opened_at"]:
        db.execute(
            "UPDATE business_applications SET opened_at = CURRENT_TIMESTAMP, opened_by = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"], application_id),
        )
        application = db.execute(
            "SELECT * FROM business_applications WHERE id = ?", (application_id,)
        ).fetchone()
    return render_template(
        "admin/application_detail.html", user=user, application=application,
        csrf_token=session["admin_csrf_token"],
    )


@admin.post("/admin/applications/<int:application_id>/continue")
def application_continue(application_id):
    user, response = _admin_or_redirect()
    if response:
        return response
    if not _csrf_valid():
        abort(400)
    cursor = get_db().execute(
        "UPDATE business_applications SET status = 'verification', "
        "verification_started_at = COALESCE(verification_started_at, CURRENT_TIMESTAMP), "
        "verification_started_by = COALESCE(verification_started_by, ?), updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND status IN ('new', 'verification')", (user["id"], application_id),
    )
    if not cursor.rowcount:
        abort(404)
    return redirect(url_for("admin.clients", application=application_id))


@admin.post("/admin/applications/<int:application_id>/reject")
def application_reject(application_id):
    user, response = _admin_or_redirect()
    if response:
        return response
    if user["role"] != "admin":
        abort(403)
    if not _csrf_valid():
        abort(400)
    cursor = get_db().execute(
        "UPDATE business_applications SET status = 'rejected', decided_at = CURRENT_TIMESTAMP, "
        "decided_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status IN ('new', 'verification')",
        (user["id"], application_id),
    )
    if not cursor.rowcount:
        abort(404)
    flash("Kërkesa për llogari u refuzua.", "success")
    return redirect(url_for("admin.dashboard"))


@admin.post("/admin/applications/<int:application_id>/verify")
def application_verify(application_id):
    user, response = _admin_or_redirect()
    if response:
        return response
    if user["role"] != "admin":
        abort(403)
    if not _csrf_valid():
        abort(400)
    values = _valid_client_form()
    db = get_db()
    application = db.execute(
        "SELECT * FROM business_applications WHERE id = ? AND status = 'verification'", (application_id,)
    ).fetchone()
    if not application or not values or not values["phone"] or not values["address"]:
        flash("Kontrolloni të dhënat e kërkuara për verifikim.", "error")
        return _client_page(user, application=application), 400
    if not current_app.config.get("SMTP_HOST") and not current_app.config.get("MAIL_SUPPRESS_SEND"):
        flash("Emaili i aktivizimit nuk u dërgua sepse SMTP_HOST nuk është konfiguruar.", "error")
        return _client_page(user, application=application), 503

    otp = f"{secrets.randbelow(100_000_000):08d}"
    minutes = current_app.config["ACTIVATION_OTP_MINUTES"]
    db.execute("BEGIN IMMEDIATE")
    try:
        client_cursor = db.execute(
            "INSERT INTO business_clients(company_name,tax_id,contact_name,email,phone,address,status,price_tier) "
            "VALUES (?,?,?,?,?,?,'pending','standard')", tuple(values.values()),
        )
        account_cursor = db.execute(
            "INSERT INTO users(email,password_hash,display_name,role,is_active,must_change_password) "
            "VALUES (?,?,?,'customer',1,1)",
            (values["email"], generate_password_hash(secrets.token_urlsafe(48), method="scrypt"), values["contact_name"]),
        )
        db.execute(
            "INSERT INTO business_members(business_id,user_id,member_role) VALUES (?,?,'owner')",
            (client_cursor.lastrowid, account_cursor.lastrowid),
        )
        db.execute(
            "INSERT INTO business_addresses(business_id,label,line1,is_default) VALUES (?,'Primary',?,1)",
            (client_cursor.lastrowid, values["address"]),
        )
        db.execute(
            "INSERT INTO account_activation_otps(user_id,otp_hash,expires_at) "
            "VALUES (?,?,datetime('now', ?))",
            (account_cursor.lastrowid, generate_password_hash(otp, method="scrypt"), f"+{minutes} minutes"),
        )
        send_activation_email(values["email"], values["contact_name"], values["company_name"], otp)
        db.execute(
            "UPDATE business_clients SET status = 'approved', verified_at = CURRENT_TIMESTAMP, verified_by = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"], client_cursor.lastrowid),
        )
        db.execute(
            "UPDATE business_applications SET status = 'approved', business_id = ?, decided_at = CURRENT_TIMESTAMP, "
            "decided_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (client_cursor.lastrowid, user["id"], application_id),
        )
        db.execute("COMMIT")
    except Exception as error:
        if db.in_transaction:
            db.execute("ROLLBACK")
        if "UNIQUE constraint failed" in str(error):
            flash("Ky email biznesi ose NIPT është regjistruar më parë.", "error")
            return _client_page(user, application=application), 409
        current_app.logger.exception("Could not approve account request")
        flash("Emaili i aktivizimit nuk u dërgua. Kontrolloni SMTP dhe provoni përsëri.", "error")
        return _client_page(user, application=application), 502
    flash("Klienti u verifikua. Kodi njëpërdorimësh u dërgua në emailin e biznesit.", "success")
    return redirect(url_for("admin.dashboard"))


@admin.route("/admin/clients", methods=["GET", "POST"])
def clients():
    user, response = _admin_or_redirect()
    if response:
        return response
    if request.method == "GET":
        return _client_page(user)
    if user["role"] != "admin":
        abort(403)
    if not _csrf_valid():
        abort(400)
    values = _valid_client_form()
    if not values:
        flash("Kontrolloni të dhënat e kërkuara të klientit.", "error")
        return _client_page(user), 400

    password = secrets.token_urlsafe(12)
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        client_cursor = db.execute(
            "INSERT INTO business_clients(company_name, tax_id, contact_name, email, phone, address, status, verified_at, verified_by) "
            "VALUES (?, ?, ?, ?, ?, ?, 'approved', CURRENT_TIMESTAMP, ?)",
            (*values.values(), user["id"]),
        )
        account_cursor = db.execute(
            "INSERT INTO users(email, password_hash, display_name, role, must_change_password) VALUES (?, ?, ?, 'customer', 1)",
            (values["email"], generate_password_hash(password, method="scrypt"), values["contact_name"]),
        )
        db.execute(
            "INSERT INTO business_members(business_id, user_id, member_role) VALUES (?, ?, 'owner')",
            (client_cursor.lastrowid, account_cursor.lastrowid),
        )
        db.execute("COMMIT")
    except Exception as error:
        db.execute("ROLLBACK")
        if "UNIQUE constraint failed" in str(error):
            flash("Ky email ose NIPT është regjistruar më parë.", "error")
            return _client_page(user), 409
        raise
    return _client_page(
        user,
        {"company": values["company_name"], "email": values["email"], "password": password},
    ), 201


@admin.post("/admin/clients/<int:client_id>/approve")
def approve_client(client_id):
    user, response = _admin_or_redirect()
    if response:
        return response
    if user["role"] != "admin":
        abort(403)
    if not _csrf_valid():
        abort(400)
    db = get_db()
    client = db.execute(
        "SELECT * FROM business_clients WHERE id = ? AND status = 'pending'", (client_id,)
    ).fetchone()
    if not client:
        abort(404)
    existing_owner = db.execute(
        "SELECT u.id FROM business_members m JOIN users u ON u.id=m.user_id "
        "WHERE m.business_id=? AND m.member_role='owner' LIMIT 1", (client_id,)
    ).fetchone()
    if existing_owner:
        db.execute(
            "UPDATE business_clients SET status='approved',verified_at=CURRENT_TIMESTAMP,verified_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (user["id"], client_id),
        )
        flash("Biznesi shtesë u miratua dhe u lidh me llogarinë ekzistuese.", "success")
        return redirect(url_for("admin.dashboard"))
    password = secrets.token_urlsafe(12)
    db.execute("BEGIN IMMEDIATE")
    try:
        account_cursor = db.execute(
            "INSERT INTO users(email, password_hash, display_name, role, must_change_password) VALUES (?, ?, ?, 'customer', 1)",
            (client["email"], generate_password_hash(password, method="scrypt"), client["contact_name"]),
        )
        db.execute(
            "INSERT INTO business_members(business_id, user_id, member_role) VALUES (?, ?, 'owner')",
            (client_id, account_cursor.lastrowid),
        )
        db.execute(
            "UPDATE business_clients SET status = 'approved', verified_at = CURRENT_TIMESTAMP, "
            "verified_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user["id"], client_id),
        )
        db.execute("COMMIT")
    except Exception as error:
        db.execute("ROLLBACK")
        if "UNIQUE constraint failed" in str(error):
            flash("Një llogari tjetër përdor këtë email biznesi.", "error")
            return _client_page(user), 409
        raise
    return _client_page(
        user,
        {"company": client["company_name"], "email": client["email"], "password": password},
    )


@admin.post("/admin/clients/<int:client_id>/update")
def update_client(client_id):
    user, response = _admin_or_redirect()
    if response:
        return response
    if user["role"] != "admin":
        abort(403)
    if not _csrf_valid():
        abort(400)
    status = request.form.get("status", "")
    price_tier = request.form.get("price_tier", "")
    if status not in CLIENT_STATUSES or price_tier not in {"standard", "trade"}:
        abort(400)
    cursor = get_db().execute(
        "UPDATE business_clients SET status = ?, price_tier = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, price_tier, client_id),
    )
    if not cursor.rowcount:
        abort(404)
    flash("Cilësimet e klientit u përditësuan.", "success")
    return redirect(url_for("admin.dashboard"))


@admin.post("/admin/clients/<int:client_id>/reset-password")
def reset_client_password(client_id):
    user, response = _admin_or_redirect()
    if response:
        return response
    if user["role"] != "admin":
        abort(403)
    if not _csrf_valid():
        abort(400)
    client = get_db().execute(
        "SELECT b.company_name, u.id AS user_id, u.email FROM business_clients b "
        "JOIN business_members m ON m.business_id = b.id JOIN users u ON u.id = m.user_id "
        "WHERE b.id = ? AND m.member_role = 'owner' ORDER BY u.id LIMIT 1", (client_id,)
    ).fetchone()
    if not client:
        abort(404)
    password = secrets.token_urlsafe(12)
    get_db().execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1, auth_version = auth_version + 1 WHERE id = ?",
        (generate_password_hash(password, method="scrypt"), client["user_id"]),
    )
    return _client_page(
        user, {"company": client["company_name"], "email": client["email"], "password": password}
    )


@admin.post("/admin/clients/<int:client_id>/resend-activation")
def resend_client_activation(client_id):
    user, response = _admin_or_redirect()
    if response:
        return response
    if user["role"] != "admin":
        abort(403)
    if not _csrf_valid():
        abort(400)
    if not current_app.config.get("SMTP_HOST") and not current_app.config.get("MAIL_SUPPRESS_SEND"):
        flash("Emaili i aktivizimit nuk u dërgua sepse SMTP_HOST nuk është konfiguruar.", "error")
        return redirect(url_for("admin.clients"))
    client = get_db().execute(
        "SELECT b.company_name, b.contact_name, b.email, u.id AS user_id, u.must_change_password "
        "FROM business_clients b JOIN business_members m ON m.business_id = b.id "
        "JOIN users u ON u.id = m.user_id WHERE b.id = ? AND m.member_role = 'owner' LIMIT 1",
        (client_id,),
    ).fetchone()
    if not client:
        abort(404)
    if not client["must_change_password"]:
        flash("Ky klient e ka krijuar tashmë fjalëkalimin e përhershëm.", "error")
        return redirect(url_for("admin.clients"))
    otp = f"{secrets.randbelow(100_000_000):08d}"
    minutes = current_app.config["ACTIVATION_OTP_MINUTES"]
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "INSERT INTO account_activation_otps(user_id,otp_hash,attempts,expires_at) "
            "VALUES (?,?,0,datetime('now',?)) ON CONFLICT(user_id) DO UPDATE SET "
            "otp_hash=excluded.otp_hash,attempts=0,expires_at=excluded.expires_at,created_at=CURRENT_TIMESTAMP",
            (client["user_id"], generate_password_hash(otp, method="scrypt"), f"+{minutes} minutes"),
        )
        send_activation_email(client["email"], client["contact_name"], client["company_name"], otp)
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        current_app.logger.exception("Could not resend activation code")
        flash("Emaili i aktivizimit nuk u dërgua. Kontrolloni SMTP dhe provoni përsëri.", "error")
        return redirect(url_for("admin.clients"))
    flash("Një kod i ri njëpërdorimësh iu dërgua klientit me email.", "success")
    return redirect(url_for("admin.dashboard"))


@admin.get("/admin/orders")
def order_list():
    user, response = _admin_or_redirect()
    if response:
        return response
    db = get_db()
    orders = db.execute(
        "SELECT o.id, o.reference, o.status, o.delivery_address, o.created_at, o.total_cents, o.currency, b.company_name, "
        "COUNT(i.id) AS item_count FROM orders o "
        "JOIN business_clients b ON b.id = o.business_id "
        "LEFT JOIN order_items i ON i.order_id = o.id "
        "GROUP BY o.id ORDER BY o.id DESC"
    ).fetchall()
    latest_order_event_id = db.execute("SELECT COALESCE(MAX(id),0) FROM order_status_events").fetchone()[0]
    return render_template(
        "admin/orders.html", user=user, orders=orders, latest_order_event_id=latest_order_event_id,
        csrf_token=session["admin_csrf_token"]
    )


@admin.get("/admin/orders/<int:order_id>")
def order_detail(order_id):
    user, response = _admin_or_redirect()
    if response:
        return response
    db = get_db()
    order = db.execute(
        "SELECT o.*, b.company_name, b.email AS business_email, u.display_name AS submitted_name "
        "FROM orders o JOIN business_clients b ON b.id = o.business_id "
        "JOIN users u ON u.id = o.submitted_by WHERE o.id = ?", (order_id,)
    ).fetchone()
    if not order:
        abort(404)
    lines = db.execute(
        "SELECT i.*, p.sku, p.name_sq FROM order_items i JOIN products p ON p.id = i.product_id "
        "WHERE i.order_id = ? ORDER BY i.id", (order_id,)
    ).fetchall()
    history = db.execute(
        "SELECT e.status, e.created_at, u.display_name FROM order_status_events e "
        "LEFT JOIN users u ON u.id = e.changed_by WHERE e.order_id = ? ORDER BY e.id DESC",
        (order_id,),
    ).fetchall()
    change = db.execute(
        "SELECT r.*,u.display_name AS requested_name,d.display_name AS decided_name "
        "FROM order_change_requests r JOIN users u ON u.id=r.requested_by "
        "LEFT JOIN users d ON d.id=r.decided_by WHERE r.order_id=? ORDER BY r.id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    change_lines = db.execute(
        "SELECT i.*,p.sku,p.name_sq FROM order_change_request_items i JOIN products p ON p.id=i.product_id "
        "WHERE i.request_id=? ORDER BY i.id", (change["id"],),
    ).fetchall() if change else []
    return render_template("admin/order_detail.html", user=user, order=order, lines=lines,
                           history=history, change=change, change_lines=change_lines,
                           csrf_token=session["admin_csrf_token"])


def _operation_orders(role):
    status_filter = " WHERE o.status=?" if role == "delivery" else ""
    parameters = ("shipped",) if role == "delivery" else ()
    return get_db().execute(
        "SELECT o.id,o.reference,o.status,o.delivery_address,o.note,o.created_at,"
        "o.total_cents,o.currency,b.company_name,b.phone,COUNT(i.id) AS item_count,"
        "EXISTS(SELECT 1 FROM order_change_requests r WHERE r.order_id=o.id AND r.status='pending') AS has_pending_change "
        "FROM orders o JOIN business_clients b ON b.id=o.business_id "
        "LEFT JOIN order_items i ON i.order_id=o.id" + status_filter + " GROUP BY o.id ORDER BY o.id DESC",
        parameters,
    ).fetchall()


def _operation_order_json(order, role):
    action = None
    pending_change = bool(order["has_pending_change"])
    if role == "packing" and not pending_change and order["status"] in {"submitted", "confirmed"}:
        action = {"status": "processing", "label": "Fillo paketimin"}
    elif role == "packing" and not pending_change and order["status"] == "processing":
        action = {"status": "shipped", "label": "Gati për dorëzim"}
    elif role == "delivery" and not pending_change and order["status"] == "shipped":
        action = {"status": "completed", "label": "Shëno si të dorëzuar"}
    return {
        "id": order["id"], "reference": order["reference"], "status": order["status"],
        "deliveryAddress": order["delivery_address"], "note": order["note"],
        "createdAt": order["created_at"], "totalCents": order["total_cents"] or 0,
        "currency": order["currency"], "companyName": order["company_name"],
        "phone": order["phone"], "itemCount": order["item_count"], "action": action,
        "hasPendingChange": pending_change,
        "requiresAction": bool(action) or (role == "packing" and pending_change),
        "url": url_for("admin.operation_order", order_id=order["id"]),
        "actionUrl": url_for("admin.operation_order_status", order_id=order["id"]),
    }


@admin.get("/operations")
def operations():
    user, role, response = _operation_or_redirect()
    if response:
        return response
    return redirect(url_for(f"admin.{role}_panel"))


def _render_operation_panel(required_role):
    user, role, response = _operation_or_redirect()
    if response:
        return response
    if role != required_role:
        return redirect(url_for(f"admin.{role}_panel"))
    orders = _operation_orders(role)
    order_values = [_operation_order_json(order, role) for order in orders]
    return render_template(
        "admin/operations.html", user=user, role=role, orders=orders,
        order_values=order_values, action_count=sum(value["requiresAction"] for value in order_values),
        csrf_token=session["admin_csrf_token"],
    )


@admin.get("/operations/packing")
def packing_panel():
    return _render_operation_panel("packing")


@admin.get("/operations/delivery")
def delivery_panel():
    return _render_operation_panel("delivery")


@admin.get("/operations/live")
def operation_live():
    user, role, response = _operation_or_redirect()
    if response:
        return jsonify(error="Kërkohet hyrja në llogari."), 401
    values=[_operation_order_json(order, role) for order in _operation_orders(role)]
    return jsonify(role=role,orders=values,actionCount=sum(value["requiresAction"] for value in values))


@admin.get("/operations/orders/<int:order_id>")
def operation_order(order_id):
    user, role, response = _operation_or_redirect()
    if response:
        return response
    db = get_db()
    order = db.execute(
        "SELECT o.*,b.company_name,b.phone,"
        "EXISTS(SELECT 1 FROM order_change_requests r WHERE r.order_id=o.id AND r.status='pending') AS has_pending_change "
        "FROM orders o JOIN business_clients b ON b.id=o.business_id WHERE o.id=?", (order_id,),
    ).fetchone()
    if not order or (role == "delivery" and order["status"] != "shipped"):
        abort(404)
    lines = db.execute(
        "SELECT i.*, p.sku, p.name_sq FROM order_items i JOIN products p ON p.id=i.product_id "
        "WHERE i.order_id=? ORDER BY i.id", (order_id,),
    ).fetchall()
    history = db.execute(
        "SELECT e.status,e.created_at,u.display_name FROM order_status_events e "
        "LEFT JOIN users u ON u.id=e.changed_by WHERE e.order_id=? ORDER BY e.id DESC", (order_id,),
    ).fetchall()
    change = db.execute(
        "SELECT r.*,u.display_name AS requested_name,d.display_name AS decided_name "
        "FROM order_change_requests r JOIN users u ON u.id=r.requested_by "
        "LEFT JOIN users d ON d.id=r.decided_by WHERE r.order_id=? ORDER BY r.id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    change_lines = db.execute(
        "SELECT i.*,p.sku,p.name_sq FROM order_change_request_items i JOIN products p ON p.id=i.product_id "
        "WHERE i.request_id=? ORDER BY i.id", (change["id"],),
    ).fetchall() if change else []
    return render_template(
        "admin/operation_order.html", user=user, role=role, order=order, lines=lines,
        history=history,change=change,change_lines=change_lines,
        order_value=_operation_order_json({**dict(order), "item_count": len(lines)}, role),
        csrf_token=session["admin_csrf_token"],
    )


@admin.post("/operations/orders/<int:order_id>/changes/<int:change_id>/<decision>")
def operation_order_change(order_id, change_id, decision):
    from .commerce import _adjust_inventory, _unit_price

    user, role, response = _operation_or_redirect()
    if response:
        return response
    if role != "packing":
        abort(403)
    if not _csrf_valid() or decision not in {"accept", "reject"}:
        abort(400)
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        change = db.execute(
            "SELECT r.*,o.status AS order_status,b.price_tier FROM order_change_requests r "
            "JOIN orders o ON o.id=r.order_id JOIN business_clients b ON b.id=o.business_id "
            "WHERE r.id=? AND r.order_id=? AND r.status='pending'", (change_id, order_id),
        ).fetchone()
        if not change:
            db.execute("ROLLBACK")
            abort(409)
        if decision == "reject":
            db.execute(
                "UPDATE order_change_requests SET status='rejected',decided_by=?,decided_at=CURRENT_TIMESTAMP WHERE id=?",
                (user["id"], change_id),
            )
            resulting_status = change["order_status"]
        else:
            if change["order_status"] not in {"submitted", "confirmed", "processing"}:
                db.execute("ROLLBACK")
                abort(409)
            previous = {row["product_id"]: row["quantity"] for row in db.execute(
                "SELECT product_id,quantity FROM order_items WHERE order_id=?", (order_id,)
            )}
            requested = db.execute(
                "SELECT i.product_id,i.quantity,p.* FROM order_change_request_items i "
                "JOIN products p ON p.id=i.product_id WHERE i.request_id=? ORDER BY i.id", (change_id,),
            ).fetchall()
            if not requested:
                db.execute("ROLLBACK")
                abort(409)
            subtotal=vat=0;priced=[]
            for product in requested:
                quantity=product["quantity"]
                available=product["available_units"]
                if not product["active"] or (available is not None and quantity > available + previous.get(product["product_id"],0)):
                    db.execute("ROLLBACK")
                    flash("Ndryshimi nuk mund të pranohet sepse stoku nuk është i mjaftueshëm.", "error")
                    return redirect(url_for("admin.operation_order", order_id=order_id))
                unit=_unit_price(product,quantity,change["price_tier"]);line=unit*quantity
                subtotal+=line;vat+=round(line*product["vat_basis_points"]/10000)
                priced.append((product["product_id"],quantity,unit,line))
            current={product_id:quantity for product_id,quantity,_,_ in priced}
            for product_id in set(previous)|set(current):
                delta=current.get(product_id,0)-previous.get(product_id,0)
                if delta and not _adjust_inventory(db,product_id,delta):
                    db.execute("ROLLBACK")
                    flash("Ndryshimi nuk mund të pranohet sepse stoku nuk është i mjaftueshëm.", "error")
                    return redirect(url_for("admin.operation_order", order_id=order_id))
                if delta:
                    db.execute("UPDATE products SET monthly_units_sold=MAX(0,monthly_units_sold+?) WHERE id=?",(delta,product_id))
            db.execute("DELETE FROM order_items WHERE order_id=?",(order_id,))
            db.executemany(
                "INSERT INTO order_items(order_id,product_id,quantity,unit_price_cents,line_total_cents) VALUES (?,?,?,?,?)",
                [(order_id,*line) for line in priced],
            )
            db.execute(
                "UPDATE orders SET status='submitted',server_total_cents=?,subtotal_cents=?,vat_cents=?,total_cents=?,currency='ALL',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (subtotal+vat,subtotal,vat,subtotal+vat,order_id),
            )
            db.execute(
                "UPDATE order_change_requests SET status='accepted',subtotal_cents=?,vat_cents=?,total_cents=?,"
                "decided_by=?,decided_at=CURRENT_TIMESTAMP WHERE id=?",
                (subtotal,vat,subtotal+vat,user["id"],change_id),
            )
            resulting_status = "submitted"
        db.execute("UPDATE orders SET updated_at=CURRENT_TIMESTAMP WHERE id=?",(order_id,))
        db.execute("INSERT INTO order_status_events(order_id,status,changed_by) VALUES (?,?,?)",(order_id,resulting_status,user["id"]))
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    flash("Ndryshimi i porosisë u pranua." if decision == "accept" else "Ndryshimi i porosisë u refuzua.", "success")
    return redirect(url_for("admin.operation_order", order_id=order_id))


@admin.post("/operations/orders/<int:order_id>/status")
def operation_order_status(order_id):
    user, role, response = _operation_or_redirect()
    if response:
        return response
    if not _csrf_valid():
        abort(400)
    target = request.form.get("status", "")
    transitions = {
        "packing": {"submitted": "processing", "confirmed": "processing", "processing": "shipped"},
        "delivery": {"shipped": "completed"},
    }
    db = get_db()
    order = db.execute(
        "SELECT o.status,EXISTS(SELECT 1 FROM order_change_requests r WHERE r.order_id=o.id AND r.status='pending') AS has_pending_change "
        "FROM orders o WHERE o.id=?", (order_id,)
    ).fetchone()
    if order and order["has_pending_change"]:
        abort(409)
    if not order or transitions[role].get(order["status"]) != target:
        abort(409)
    db.execute("BEGIN IMMEDIATE")
    try:
        cursor = db.execute(
            "UPDATE orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=? "
            "AND NOT EXISTS(SELECT 1 FROM order_change_requests r WHERE r.order_id=orders.id AND r.status='pending')",
            (target, order_id, order["status"]),
        )
        if not cursor.rowcount:
            raise RuntimeError("Order status changed before this update was applied.")
        db.execute("INSERT INTO order_status_events(order_id,status,changed_by) VALUES (?,?,?)", (order_id, target, user["id"]))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    flash("Statusi i porosisë u përditësua.", "success")
    return redirect(url_for(f"admin.{role}_panel"))


@admin.route("/admin/catalog", methods=["GET", "POST"])
def catalog():
    user, response = _admin_or_redirect()
    if response:
        return response
    db = get_db()
    if request.method == "POST":
        if user["role"] != "admin":
            abort(403)
        if not _csrf_valid():
            abort(400)
        product_id = request.form.get("product_id", "")
        availability = request.form.get("availability", "")
        try:
            price_cents = int((Decimal(request.form.get("price_cents", "")) * 100).quantize(Decimal("1")))
            units_text = request.form.get("available_units", "").strip()
            available_units = int(units_text) if units_text else None
        except (InvalidOperation, ValueError):
            abort(400)
        if availability not in {"in_stock", "low_stock", "out_of_stock", "preorder"} or price_cents < 0 or (available_units is not None and available_units < 0):
            abort(400)
        cursor = db.execute(
            "UPDATE products SET unit_price_cents = ?, available_units = ?, availability = ?, active = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (price_cents, available_units, availability, 1 if request.form.get("active") else 0, product_id),
        )
        if not cursor.rowcount:
            abort(404)
        flash("Produkti u përditësua.", "success")
        return redirect(url_for("admin.dashboard"))
    products = db.execute(
        "SELECT p.*, c.name_sq AS category_name, b.name AS brand_name FROM products p "
        "JOIN categories c ON c.id = p.category_id JOIN brands b ON b.id = p.brand_id "
        "ORDER BY c.name_sq, p.name_sq"
    ).fetchall()
    return render_template("admin/catalog.html", user=user, products=products,
                           csrf_token=session["admin_csrf_token"])


@admin.get("/admin/quotes")
def quote_list():
    user, response = _admin_or_redirect()
    if response:
        return response
    quotes = get_db().execute(
        "SELECT q.id, q.reference, q.status, q.estimated_total_cents, q.currency, q.created_at, "
        "b.company_name, COUNT(i.id) AS item_count FROM quotes q "
        "JOIN business_clients b ON b.id = q.business_id LEFT JOIN quote_items i ON i.quote_id = q.id "
        "GROUP BY q.id ORDER BY q.id DESC LIMIT 200"
    ).fetchall()
    return render_template("admin/quotes.html", user=user, quotes=quotes,
                           csrf_token=session["admin_csrf_token"])


@admin.route("/admin/quotes/<int:quote_id>", methods=["GET", "POST"])
def quote_detail(quote_id):
    user, response = _admin_or_redirect()
    if response:
        return response
    db = get_db()
    quote = db.execute(
        "SELECT q.*, b.company_name, b.email AS business_email, u.display_name AS submitted_name "
        "FROM quotes q JOIN business_clients b ON b.id = q.business_id "
        "JOIN users u ON u.id = q.submitted_by WHERE q.id = ?", (quote_id,)
    ).fetchone()
    if not quote:
        abort(404)
    if request.method == "POST":
        if not _csrf_valid():
            abort(400)
        status = request.form.get("status", "")
        if status not in QUOTE_STATUSES:
            abort(400)
        db.execute("UPDATE quotes SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, quote_id))
        flash("Statusi i ofertës u përditësua.", "success")
        return redirect(url_for("admin.dashboard"))
    lines = db.execute(
        "SELECT i.*, p.sku, p.name_sq FROM quote_items i JOIN products p ON p.id = i.product_id "
        "WHERE i.quote_id = ? ORDER BY i.id", (quote_id,)
    ).fetchall()
    return render_template("admin/quote_detail.html", user=user, quote=quote, lines=lines,
                           statuses=QUOTE_STATUSES, csrf_token=session["admin_csrf_token"])


@admin.get("/admin/analytics")
def analytics():
    user, response = _admin_or_redirect()
    if response:
        return response
    db = get_db()
    last_30_days = db.execute(
        "SELECT COUNT(*) FROM orders WHERE created_at >= datetime('now', '-30 days')"
    ).fetchone()[0]
    previous_30_days = db.execute(
        "SELECT COUNT(*) FROM orders WHERE created_at >= datetime('now', '-60 days') "
        "AND created_at < datetime('now', '-30 days')"
    ).fetchone()[0]
    total_items = db.execute(
        "SELECT COALESCE(SUM(i.quantity), 0) FROM order_items i JOIN orders o ON o.id = i.order_id "
        "WHERE o.created_at >= datetime('now', '-30 days')"
    ).fetchone()[0]
    status_totals = db.execute(
        "SELECT status, COUNT(*) AS total FROM orders "
        "WHERE created_at >= datetime('now', '-30 days') GROUP BY status ORDER BY total DESC"
    ).fetchall()
    client_totals = db.execute(
        "SELECT b.company_name, COUNT(o.id) AS order_count, "
        "COALESCE(SUM((SELECT SUM(quantity) FROM order_items WHERE order_id = o.id)), 0) AS item_count "
        "FROM business_clients b JOIN orders o ON o.business_id = b.id "
        "WHERE o.created_at >= datetime('now', '-30 days') "
        "GROUP BY b.id ORDER BY order_count DESC, b.company_name LIMIT 20"
    ).fetchall()
    daily_totals = db.execute(
        "SELECT date(created_at) AS order_date, COUNT(*) AS order_count "
        "FROM orders WHERE created_at >= datetime('now', '-30 days') "
        "GROUP BY date(created_at) ORDER BY order_date DESC"
    ).fetchall()
    return render_template(
        "admin/analytics.html",
        user=user,
        last_30_days=last_30_days,
        previous_30_days=previous_30_days,
        total_items=total_items,
        status_totals=status_totals,
        client_totals=client_totals,
        daily_totals=daily_totals,
        csrf_token=session["admin_csrf_token"],
    )


@admin.post("/admin/logout")
def logout():
    if not _csrf_valid():
        abort(400)
    session.clear()
    response = redirect(url_for("admin.login_page"))
    response.delete_cookie(
        current_app.config["SESSION_COOKIE_NAME"],
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response
