import hmac
import re
import secrets

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import _DUMMY_HASH, _rate_limited
from .database import get_db

admin = Blueprint(
    "admin",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/admin/static",
)

TEMPORARY_ADMIN_EMAIL = "admin@dukagroup.al"
TEMPORARY_ADMIN_PASSWORD = "DukaGroupAdmin2026!"


def bootstrap_first_admin():
    """Create the temporary admin account automatically when no admin exists."""
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        existing = db.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if existing:
            db.execute("COMMIT")
            return
        db.execute(
            "INSERT INTO users(email, password_hash, display_name, role) VALUES (?, ?, ?, 'admin')",
            (
                TEMPORARY_ADMIN_EMAIL,
                generate_password_hash(TEMPORARY_ADMIN_PASSWORD, method="scrypt"),
                "DUKA Administrator",
            ),
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
    return user


def _admin_or_redirect():
    user = _current_admin()
    if not user:
        return None, redirect(url_for("admin.login_page"))
    return user, None


def _client_page(user, new_credentials=None):
    clients = get_db().execute(
        "SELECT b.*, u.id AS user_id FROM business_clients b "
        "LEFT JOIN business_members m ON m.business_id = b.id "
        "LEFT JOIN users u ON u.id = m.user_id "
        "ORDER BY CASE b.status WHEN 'pending' THEN 0 ELSE 1 END, b.id DESC"
    ).fetchall()
    return render_template(
        "admin/clients.html",
        user=user,
        clients=clients,
        csrf_token=session["admin_csrf_token"],
        new_credentials=new_credentials,
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
        return redirect(url_for("admin.dashboard"))
    session["admin_csrf_token"] = secrets.token_urlsafe(32)
    return render_template("admin/login.html")


@admin.post("/admin/login")
def login_submit():
    if not _csrf_valid():
        abort(400)
    email = request.form.get("email", "").strip().casefold()
    password = request.form.get("password", "")
    if len(email) > 254 or len(password) > 256 or not email or not password:
        flash("Email or password is incorrect.", "error")
        return redirect(url_for("admin.login_page"))

    if _rate_limited(email):
        flash("Too many sign-in attempts. Try again shortly.", "error")
        return render_template("admin/login.html"), 429

    user = get_db().execute(
        "SELECT id, email, password_hash, display_name, role, is_active, auth_version "
        "FROM users WHERE email = ? COLLATE NOCASE",
        (email,),
    ).fetchone()
    matches = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
    if not user or not matches or not user["is_active"] or user["role"] not in {"staff", "admin"}:
        flash("Email or password is incorrect.", "error")
        return redirect(url_for("admin.login_page")), 401

    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["auth_version"] = user["auth_version"]
    session["admin_csrf_token"] = secrets.token_urlsafe(32)
    return redirect(url_for("admin.dashboard"))


@admin.get("/admin")
def dashboard():
    user = _current_admin()
    if not user:
        return redirect(url_for("admin.login_page"))
    db = get_db()
    account_count = db.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
    staff_count = db.execute(
        "SELECT COUNT(*) FROM users WHERE is_active = 1 AND role IN ('staff', 'admin')"
    ).fetchone()[0]
    pending_clients = db.execute("SELECT COUNT(*) FROM business_clients WHERE status = 'pending'").fetchone()[0]
    order_count = db.execute(
        "SELECT COUNT(*) FROM orders WHERE created_at >= datetime('now', '-30 days')"
    ).fetchone()[0]
    return render_template(
        "admin/dashboard.html",
        user=user,
        account_count=account_count,
        staff_count=staff_count,
        pending_clients=pending_clients,
        order_count=order_count,
        csrf_token=session["admin_csrf_token"],
    )


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
        flash("Check the required client details.", "error")
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
            "INSERT INTO users(email, password_hash, display_name, role) VALUES (?, ?, ?, 'customer')",
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
            flash("That email or tax ID is already registered.", "error")
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
    password = secrets.token_urlsafe(12)
    db.execute("BEGIN IMMEDIATE")
    try:
        account_cursor = db.execute(
            "INSERT INTO users(email, password_hash, display_name, role) VALUES (?, ?, ?, 'customer')",
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
            flash("An account already uses this business email.", "error")
            return _client_page(user), 409
        raise
    return _client_page(
        user,
        {"company": client["company_name"], "email": client["email"], "password": password},
    )


@admin.get("/admin/orders")
def order_list():
    user, response = _admin_or_redirect()
    if response:
        return response
    orders = get_db().execute(
        "SELECT o.reference, o.status, o.delivery_address, o.created_at, b.company_name, "
        "COUNT(i.id) AS item_count FROM orders o "
        "JOIN business_clients b ON b.id = o.business_id "
        "LEFT JOIN order_items i ON i.order_id = o.id "
        "GROUP BY o.id ORDER BY o.id DESC LIMIT 200"
    ).fetchall()
    return render_template(
        "admin/orders.html", user=user, orders=orders, csrf_token=session["admin_csrf_token"]
    )


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
