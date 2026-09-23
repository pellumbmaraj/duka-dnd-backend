import re
import secrets

from flask import Blueprint, jsonify, request, session

from .auth import _csrf_ok, _valid_origin
from .database import get_db

commerce = Blueprint("commerce", __name__)


def _json_body():
    if not request.is_json:
        return None
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else None


def _clean_text(value, maximum, required=False):
    if not isinstance(value, str):
        return None if required else ""
    value = value.strip()
    if len(value) > maximum or (required and not value):
        return None
    return value


def _preflight_or_verified():
    if request.method == "OPTIONS":
        return "", 204
    if not _valid_origin() or not _csrf_ok():
        return jsonify(error="Request could not be verified."), 403
    return None


@commerce.route("/businesses/applications", methods=["POST", "OPTIONS"])
def submit_business_application():
    verification_error = _preflight_or_verified()
    if verification_error:
        return verification_error
    payload = _json_body()
    if payload is None:
        return jsonify(error="Invalid request."), 400

    company_name = _clean_text(payload.get("companyName"), 200, required=True)
    tax_id = _clean_text(payload.get("taxId"), 50, required=True)
    contact_name = _clean_text(payload.get("contactName"), 100, required=True)
    email = _clean_text(payload.get("email"), 254, required=True)
    phone = _clean_text(payload.get("phone"), 50)
    address = _clean_text(payload.get("address"), 500)
    if email:
        email = email.casefold()
    if not all((company_name, tax_id, contact_name, email)) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return jsonify(error="Check the required business details."), 400

    try:
        cursor = get_db().execute(
            "INSERT INTO business_clients(company_name, tax_id, contact_name, email, phone, address) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (company_name, tax_id, contact_name, email, phone, address),
        )
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            return jsonify(error="A business with that email or tax ID already exists."), 409
        raise
    return jsonify(application={"id": cursor.lastrowid, "status": "pending"}), 201


def _approved_business_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute(
        "SELECT u.id AS user_id, u.is_active, u.auth_version, b.id AS business_id, "
        "b.company_name, b.status FROM users u "
        "JOIN business_members m ON m.user_id = u.id "
        "JOIN business_clients b ON b.id = m.business_id "
        "WHERE u.id = ?",
        (user_id,),
    ).fetchone()


@commerce.route("/orders", methods=["GET", "POST", "OPTIONS"])
def orders():
    if request.method == "OPTIONS":
        return "", 204
    if not _valid_origin():
        return jsonify(error="Request origin is not allowed."), 403

    account = _approved_business_user()
    if (
        not account
        or not account["is_active"]
        or account["auth_version"] != session.get("auth_version")
        or account["status"] != "approved"
    ):
        return jsonify(error="An approved business account is required."), 403

    if request.method == "GET":
        rows = get_db().execute(
            "SELECT id, reference, status, delivery_address, note, server_total_cents, created_at "
            "FROM orders WHERE business_id = ? ORDER BY id DESC LIMIT 100",
            (account["business_id"],),
        ).fetchall()
        return jsonify(orders=[dict(row) for row in rows])

    if not _csrf_ok():
        return jsonify(error="Request could not be verified."), 403
    payload = _json_body()
    if payload is None:
        return jsonify(error="Invalid request."), 400
    address = _clean_text(payload.get("address"), 500, required=True)
    note = _clean_text(payload.get("note"), 1000)
    raw_items = payload.get("items")
    if not address or not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 100:
        return jsonify(error="An address and 1 to 100 order items are required."), 400

    merged = {}
    for item in raw_items:
        if not isinstance(item, dict):
            return jsonify(error="Invalid order item."), 400
        product_id = _clean_text(item.get("productId"), 100, required=True)
        quantity = item.get("qty")
        if not product_id or not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 9999:
            return jsonify(error="Invalid order item."), 400
        merged[product_id] = merged.get(product_id, 0) + quantity
        if merged[product_id] > 9999:
            return jsonify(error="Product quantity is too large."), 400

    idempotency_key = _clean_text(request.headers.get("Idempotency-Key"), 100)
    db = get_db()
    if idempotency_key:
        existing = db.execute(
            "SELECT id, reference, status FROM orders WHERE business_id = ? AND idempotency_key = ?",
            (account["business_id"], idempotency_key),
        ).fetchone()
        if existing:
            return jsonify(order=dict(existing), duplicate=True), 200

    reference = "ORD-" + secrets.token_hex(6).upper()
    db.execute("BEGIN IMMEDIATE")
    try:
        cursor = db.execute(
            "INSERT INTO orders(reference, business_id, submitted_by, delivery_address, note, idempotency_key) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (reference, account["business_id"], account["user_id"], address, note, idempotency_key),
        )
        order_id = cursor.lastrowid
        db.executemany(
            "INSERT INTO order_items(order_id, product_id, quantity) VALUES (?, ?, ?)",
            [(order_id, product_id, quantity) for product_id, quantity in merged.items()],
        )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return jsonify(order={"id": order_id, "reference": reference, "status": "submitted"}), 201
