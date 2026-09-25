import json
import math
import re
import secrets
import sqlite3
from datetime import date, timedelta

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import _csrf_ok, _valid_origin
from .database import get_db

commerce = Blueprint("commerce", __name__)
PAYMENTS = {"cod", "pickup", "invoice"}
ORDER_STATUSES = {"submitted", "confirmed", "processing", "shipped", "completed", "cancelled"}
CURRENCY = "ALL"


def _problem(code, message, status=400, fields=None):
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return jsonify(error=error), status


def _body():
    value = request.get_json(silent=True) if request.is_json else None
    return value if isinstance(value, dict) else None


def _text(value, maximum, required=False):
    if not isinstance(value, str):
        return None if required else ""
    value = value.strip()
    return None if len(value) > maximum or (required and not value) else value


def _verified(csrf=False):
    if not _valid_origin():
        return _problem("origin_not_allowed", "Request origin is not allowed.", 403)
    if csrf and not _csrf_ok():
        return _problem("csrf_failed", "Request could not be verified.", 403)
    return None


def _current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = get_db().execute(
        "SELECT id, email, display_name, phone, role, is_active, auth_version, must_change_password "
        "FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not user or not user["is_active"] or user["auth_version"] != session.get("auth_version"):
        session.clear()
        return None
    return user


def _require_user(csrf=False, allow_password_setup=False):
    invalid = _verified(csrf)
    if invalid:
        return None, invalid
    user = _current_user()
    if not user:
        return None, _problem("authentication_required", "Authentication required.", 401)
    if user["must_change_password"] and session.get("initial_password_required") and not allow_password_setup:
        return None, _problem("password_setup_required", "Create your permanent password to continue.", 403)
    return user, None


def _localized(sq, en=None):
    value = {"sq": sq or ""}
    if en:
        value["en"] = en
    return value


def _address_dto(row):
    return {
        "id": str(row["id"]), "label": row["label"], "line1": row["line1"],
        "line2": row["line2"], "city": row["city"], "postalCode": row["postal_code"],
        "countryCode": row["country_code"], "isDefault": bool(row["is_default"]),
        "taxId": row["tax_id"], "email": row["email"], "phone": row["phone"],
    }


def _business_dto(row):
    addresses = get_db().execute(
        "SELECT * FROM business_addresses WHERE business_id = ? ORDER BY is_default DESC, id", (row["id"],)
    ).fetchall()
    return {
        "id": str(row["id"]), "name": row["company_name"], "taxId": row["tax_id"],
        "email": row["email"], "phone": row["phone"], "status": row["status"],
        "priceTier": row["price_tier"], "addresses": [_address_dto(address) for address in addresses],
    }


def _user_businesses(user_id):
    return get_db().execute(
        "SELECT b.* FROM business_clients b JOIN business_members m ON m.business_id = b.id "
        "WHERE m.user_id = ? ORDER BY b.company_name", (user_id,)
    ).fetchall()


def _account_dto(user):
    return {
        "user": {
            "id": user["id"], "email": user["email"], "displayName": user["display_name"],
            "role": user["role"], "mustChangePassword": bool(user["must_change_password"]),
        },
        "businesses": [_business_dto(row) for row in _user_businesses(user["id"])],
    }


def _owned_business(user_id, business_id, approved=False):
    sql = (
        "SELECT b.* FROM business_clients b JOIN business_members m ON m.business_id = b.id "
        "WHERE m.user_id = ? AND b.id = ?"
    )
    params = [user_id, business_id]
    if approved:
        sql += " AND b.status = 'approved'"
    return get_db().execute(sql, params).fetchone()


def _price_tier(user_id):
    row = get_db().execute(
        "SELECT 1 FROM business_clients b JOIN business_members m ON m.business_id = b.id "
        "WHERE m.user_id = ? AND b.status = 'approved' AND b.price_tier = 'trade' LIMIT 1", (user_id,)
    ).fetchone()
    return "trade" if row else "standard"


def _unit_price(product, quantity, tier="standard"):
    candidates = [product["unit_price_cents"]]
    if tier == "trade":
        base = product["original_unit_price_cents"] or product["unit_price_cents"]
        candidates.append(round(base * 0.97))
    bands = get_db().execute(
        "SELECT unit_price_cents FROM product_volume_prices WHERE product_id = ? AND minimum_units <= ?",
        (product["id"], quantity),
    ).fetchall()
    candidates.extend(row["unit_price_cents"] for row in bands)
    return min(candidates)


def _product_dto(product, tier="standard"):
    category = get_db().execute("SELECT * FROM categories WHERE id = ?", (product["category_id"],)).fetchone()
    brand = get_db().execute("SELECT * FROM brands WHERE id = ?", (product["brand_id"],)).fetchone()
    bands = get_db().execute(
        "SELECT minimum_units, unit_price_cents FROM product_volume_prices WHERE product_id = ? ORDER BY minimum_units",
        (product["id"],),
    ).fetchall()
    badges = [value for value in product["badges"].split(",") if value]
    result = {
        "id": product["id"], "sku": product["sku"],
        "name": _localized(product["name_sq"], product["name_en"]),
        "category": {"id": category["id"], "slug": category["slug"], "name": _localized(category["name_sq"], category["name_en"])},
        "brand": {"id": brand["id"], "slug": brand["slug"], "name": brand["name"]},
        "imageUrl": product["image_url"], "imageAlt": _localized(product["image_alt_sq"], product["image_alt_en"]),
        "unitPriceCents": _unit_price(product, 1, tier), "currency": CURRENCY,
        "vatBasisPoints": product["vat_basis_points"], "caseSize": product["case_size"],
        "minimumOrderUnits": product["minimum_order_units"], "maximumOrderUnits": product["maximum_order_units"],
        "availability": product["availability"], "badges": badges,
        "volumePrices": [
            {"minimumUnits": row["minimum_units"], "unitPriceCents": min(row["unit_price_cents"], _unit_price(product, row["minimum_units"], tier))}
            for row in bands
        ],
        "monthlyUnitsSold": product["monthly_units_sold"],
        "active": bool(product["active"]), "updatedAt": product["updated_at"],
    }
    if brand["logo_url"]:
        result["brand"]["logoUrl"] = brand["logo_url"]
    if product["available_units"] is not None:
        result["availableUnits"] = product["available_units"]
    if product["monthly_rank"] is not None:
        result["monthlyRank"] = product["monthly_rank"]
    for key, sq_column, en_column in (
        ("description", "description_sq", "description_en"), ("ingredients", "ingredients_sq", "ingredients_en"),
        ("allergens", "allergens_sq", "allergens_en"), ("storage", "storage_sq", "storage_en"),
    ):
        if product[sq_column] or product[en_column]:
            result[key] = _localized(product[sq_column], product[en_column])
    if product["original_unit_price_cents"] is not None:
        result["originalUnitPriceCents"] = product["original_unit_price_cents"]
    if product["offer_discount_basis_points"]:
        result["offer"] = {
            "id": "offer-" + product["id"],
            "label": _localized(product["offer_label_sq"] or "Ofertë", product["offer_label_en"] or "Offer"),
            "discountBasisPoints": product["offer_discount_basis_points"],
        }
        if product["offer_ends_at"]:
            result["offer"]["endsAt"] = product["offer_ends_at"]
    return result


def _category_dto(row):
    count = get_db().execute("SELECT COUNT(*) FROM products WHERE category_id = ? AND active = 1", (row["id"],)).fetchone()[0]
    value = {"id": row["id"], "slug": row["slug"], "name": _localized(row["name_sq"], row["name_en"]), "productCount": count}
    if row["description_sq"] or row["description_en"]: value["description"] = _localized(row["description_sq"], row["description_en"])
    if row["image_url"]: value["imageUrl"] = row["image_url"]
    return value


def _brand_dto(row):
    count = get_db().execute("SELECT COUNT(*) FROM products WHERE brand_id = ? AND active = 1", (row["id"],)).fetchone()[0]
    value = {"id": row["id"], "slug": row["slug"], "name": row["name"], "productCount": count}
    if row["description_sq"] or row["description_en"]: value["description"] = _localized(row["description_sq"], row["description_en"])
    if row["logo_url"]: value["logoUrl"] = row["logo_url"]
    return value


def _validated_items(raw_items, allow_empty=False, stock_credit=None):
    minimum = 0 if allow_empty else 1
    if not isinstance(raw_items, list) or not minimum <= len(raw_items) <= 100:
        return None
    merged = {}
    for item in raw_items:
        if not isinstance(item, dict): return None
        product_id = _text(item.get("productId"), 100, True)
        quantity = item.get("quantity", item.get("qty"))
        if not product_id or not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 9999:
            return None
        merged[product_id] = merged.get(product_id, 0) + quantity
        if merged[product_id] > 9999: return None
    products = {}
    for product_id, quantity in merged.items():
        product = get_db().execute("SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)).fetchone()
        if not product or quantity < product["minimum_order_units"] or quantity > product["maximum_order_units"]:
            return None
        available = product["available_units"]
        credit = (stock_credit or {}).get(product_id, 0)
        if available is not None and quantity > available + credit:
            return None
        products[product_id] = (product, quantity)
    return products


def _cart_dto(user_id):
    tier = _price_tier(user_id)
    rows = get_db().execute(
        "SELECT c.quantity, p.* FROM cart_items c JOIN products p ON p.id = c.product_id WHERE c.user_id = ? ORDER BY c.updated_at",
        (user_id,),
    ).fetchall()
    items, warnings, subtotal, vat = [], [], 0, 0
    for product in rows:
        quantity = product["quantity"]
        unit = _unit_price(product, quantity, tier)
        line = unit * quantity
        line_vat = round(line * product["vat_basis_points"] / 10000)
        subtotal += line; vat += line_vat
        if product["availability"] in {"low_stock", "preorder"}: warnings.append(f"{product['sku']}: {product['availability']}")
        items.append({"product": _product_dto(product, tier), "quantity": quantity, "unitPriceCents": unit, "lineTotalCents": line})
    return {"items": items, "subtotalCents": subtotal, "vatCents": vat, "totalCents": subtotal + vat, "currency": CURRENCY, "warnings": warnings, "updatedAt": date.today().isoformat()}


def _adjust_inventory(db, product_id, consumed_units):
    cursor = db.execute(
        "UPDATE products SET "
        "available_units = CASE WHEN available_units IS NULL THEN NULL ELSE available_units - ? END, "
        "availability = CASE WHEN available_units IS NULL THEN availability "
        "WHEN available_units - ? <= 0 THEN 'out_of_stock' "
        "WHEN available_units - ? <= 20 THEN 'low_stock' ELSE 'in_stock' END, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ? "
        "AND (available_units IS NULL OR available_units >= ?)",
        (consumed_units, consumed_units, consumed_units, product_id, max(0, consumed_units)),
    )
    return bool(cursor.rowcount)


def _replace_cart(user_id, items):
    validated = _validated_items(items) if items else {}
    if validated is None: return None
    db = get_db(); db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
        db.executemany(
            "INSERT INTO cart_items(user_id, product_id, quantity) VALUES (?, ?, ?)",
            [(user_id, product_id, value[1]) for product_id, value in validated.items()],
        )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK"); raise
    return _cart_dto(user_id)


def _list_dto(row):
    items = get_db().execute("SELECT product_id, quantity FROM saved_list_items WHERE list_id = ? ORDER BY product_id", (row["id"],)).fetchall()
    return {"id": str(row["id"]), "name": row["name"], "items": [{"productId": item["product_id"], "quantity": item["quantity"]} for item in items], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def _page(items, page, page_size, total):
    return {"items": items, "page": page, "pageSize": page_size, "totalItems": total, "totalPages": max(1, math.ceil(total / page_size))}


def _order_dto(order, user_id):
    address = get_db().execute("SELECT * FROM business_addresses WHERE id = ?", (order["address_id"],)).fetchone()
    if not address:
        address_value = {"id": "legacy", "label": "Delivery", "line1": order["delivery_address"], "line2": "", "city": "", "postalCode": "", "countryCode": "AL", "isDefault": False}
    else: address_value = _address_dto(address)
    tier = _price_tier(user_id)
    lines = get_db().execute(
        "SELECT p.*, i.id AS line_id, i.quantity AS line_quantity, "
        "i.unit_price_cents AS snapshot_unit_price, i.line_total_cents AS snapshot_line_total "
        "FROM order_items i JOIN products p ON p.id = i.product_id "
        "WHERE i.order_id = ? ORDER BY i.id", (order["id"],)
    ).fetchall()
    items = [{"id": str(line["line_id"]), "product": _product_dto(line, tier), "quantity": line["line_quantity"], "unitPriceCents": line["snapshot_unit_price"] or 0, "lineTotalCents": line["snapshot_line_total"] or 0} for line in lines]
    change = get_db().execute(
        "SELECT id,status,subtotal_cents,vat_cents,total_cents,currency,created_at,decided_at "
        "FROM order_change_requests WHERE order_id=? ORDER BY id DESC LIMIT 1", (order["id"],)
    ).fetchone()
    result = {"id": str(order["id"]), "reference": order["reference"], "businessId": str(order["business_id"]), "status": order["status"], "deliveryAddress": address_value, "paymentMethod": order["payment_method"], "note": order["note"], "items": items, "subtotalCents": order["subtotal_cents"] or 0, "vatCents": order["vat_cents"] or 0, "totalCents": order["total_cents"] or order["server_total_cents"] or 0, "currency": order["currency"], "createdAt": order["created_at"], "updatedAt": order["updated_at"]}
    if change:
        result["amendment"] = {
            "id": str(change["id"]), "status": change["status"],
            "subtotalCents": change["subtotal_cents"], "vatCents": change["vat_cents"],
            "totalCents": change["total_cents"], "currency": change["currency"],
            "createdAt": change["created_at"], "decidedAt": change["decided_at"],
        }
    return result


def _quote_dto(quote, user_id):
    tier = _price_tier(user_id)
    lines = get_db().execute(
        "SELECT p.*, i.id AS line_id, i.quantity AS line_quantity, "
        "i.unit_price_cents AS snapshot_unit_price, i.line_total_cents AS snapshot_line_total "
        "FROM quote_items i JOIN products p ON p.id = i.product_id "
        "WHERE i.quote_id = ? ORDER BY i.id", (quote["id"],)
    ).fetchall()
    return {"id": str(quote["id"]), "reference": quote["reference"], "businessId": str(quote["business_id"]), "addressId": str(quote["address_id"]), "status": quote["status"], "note": quote["note"], "items": [{"id": str(line["line_id"]), "product": _product_dto(line, tier), "quantity": line["line_quantity"], "unitPriceCents": line["snapshot_unit_price"], "lineTotalCents": line["snapshot_line_total"]} for line in lines], "estimatedTotalCents": quote["estimated_total_cents"], "currency": quote["currency"], "createdAt": quote["created_at"], "updatedAt": quote["updated_at"]}


@commerce.get("/configuration")
def configuration():
    invalid = _verified()
    if invalid: return invalid
    return jsonify(companyName="DUKA Group", contact={"email": "info@dukagroup.al", "phone": "+355 44 540 566", "address": "Vorë, Tiranë, Albania"}, announcements=[], customerSegments=[], currency=CURRENCY)


@commerce.route("/businesses/applications", methods=["POST"])
def submit_business_application():
    invalid = _verified(True)
    if invalid: return invalid
    payload = _body()
    if payload is None: return _problem("invalid_request", "Invalid request.")
    values = {
        "company_name": _text(payload.get("companyName"), 200, True), "tax_id": _text(payload.get("taxId"), 50, True),
        "contact_name": _text(payload.get("contactName"), 100, True), "email": _text(payload.get("email"), 254, True),
        "phone": _text(payload.get("phone"), 50, True), "address": _text(payload.get("address"), 500, True),
    }
    if values["email"]: values["email"] = values["email"].casefold()
    if any(value is None for value in values.values()) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", values["email"] or ""):
        return _problem("validation_failed", "Check the required business details.", 400)
    try:
        cursor = get_db().execute(
            "INSERT INTO business_applications(company_name,tax_id,contact_name,email,phone,address) VALUES (?,?,?,?,?,?)",
            tuple(values.values()),
        )
    except sqlite3.IntegrityError:
        return _problem("application_exists", "A request with that email or tax ID is already being reviewed.", 409)
    existing = get_db().execute(
        "SELECT 1 FROM business_clients WHERE email = ? COLLATE NOCASE OR tax_id = ? COLLATE NOCASE",
        (values["email"], values["tax_id"]),
    ).fetchone()
    if existing:
        get_db().execute("DELETE FROM business_applications WHERE id = ?", (cursor.lastrowid,))
        return _problem("business_exists", "A business with that email or tax ID already exists.", 409)
    return jsonify(application={"id": cursor.lastrowid, "status": "pending", "submittedAt": date.today().isoformat()}), 201


@commerce.get("/account")
def account_get():
    user, error = _require_user()
    return error or jsonify(_account_dto(user))


@commerce.patch("/account")
def account_update():
    user, error = _require_user(True)
    if error: return error
    payload = _body() or {}; updates, params = [], []
    for key, column, maximum in (("displayName", "display_name", 100), ("email", "email", 254), ("phone", "phone", 50)):
        if key in payload:
            value = _text(payload[key], maximum, True)
            if value is None: return _problem("validation_failed", f"Invalid {key}.")
            if key == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value): return _problem("validation_failed", "Invalid email.")
            updates.append(f"{column} = ?"); params.append(value.casefold() if key == "email" else value)
    if updates:
        try: get_db().execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", (*params, user["id"]))
        except sqlite3.IntegrityError: return _problem("email_exists", "That email is already in use.", 409)
    return jsonify(_account_dto(_current_user()))


@commerce.put("/account/password")
def account_password():
    user, error = _require_user(True, allow_password_setup=True)
    if error: return error
    payload = _body() or {}; current = payload.get("currentPassword"); new = payload.get("newPassword")
    stored = get_db().execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not isinstance(current, str) or not check_password_hash(stored["password_hash"], current): return _problem("invalid_password", "Current password is incorrect.", 400)
    if not isinstance(new, str) or not 12 <= len(new) <= 256: return _problem("weak_password", "New password must be at least 12 characters.", 400)
    get_db().execute("UPDATE users SET password_hash = ?, must_change_password = 0, auth_version = auth_version + 1 WHERE id = ?", (generate_password_hash(new, method="scrypt"), user["id"]))
    session["auth_version"] = user["auth_version"] + 1
    return jsonify(message="Password changed.")


@commerce.get("/businesses")
def businesses_list():
    user, error = _require_user()
    return error or jsonify(businesses=[_business_dto(row) for row in _user_businesses(user["id"])])


@commerce.post("/businesses")
def businesses_create():
    user, error = _require_user(True)
    if error: return error
    payload = _body() or {}; name = _text(payload.get("name"), 200, True); tax_id = _text(payload.get("taxId"), 50, True); email = _text(payload.get("email"), 254, True); phone = _text(payload.get("phone"), 50)
    raw_addresses = payload.get("addresses")
    if not name or not tax_id or not email or not isinstance(raw_addresses, list) or not 1 <= len(raw_addresses) <= 10:
        return _problem("validation_failed", "Name, NIPT, email and at least one address are required.")
    addresses = [_address_input(value) for value in raw_addresses if isinstance(value, dict)]
    if len(addresses) != len(raw_addresses) or any(value is None for value in addresses):
        return _problem("validation_failed", "Check the business addresses.")
    tax_id = tax_id.upper()
    db = get_db(); db.execute("BEGIN IMMEDIATE")
    try:
        cursor = db.execute("INSERT INTO business_clients(company_name,tax_id,contact_name,email,phone,address,status) VALUES (?,?,?,?,?,?,'pending')", (name, tax_id, user["display_name"], email.casefold(), phone, addresses[0]["line1"]))
        db.execute("INSERT INTO business_members(business_id,user_id,member_role) VALUES (?,?,'owner')", (cursor.lastrowid, user["id"]))
        for index, values in enumerate(addresses):
            values["is_default"] = 1 if index == 0 else 0
            db.execute(f"INSERT INTO business_addresses(business_id,{','.join(values)}) VALUES (?,{','.join('?' for _ in values)})", (cursor.lastrowid, *values.values()))
        db.execute("COMMIT")
    except sqlite3.IntegrityError:
        db.execute("ROLLBACK"); return _problem("business_exists", "That email or tax ID is already registered.", 409)
    except Exception:
        db.execute("ROLLBACK"); raise
    return jsonify(business=_business_dto(db.execute("SELECT * FROM business_clients WHERE id = ?", (cursor.lastrowid,)).fetchone())), 201


@commerce.route("/businesses/<int:business_id>", methods=["PATCH", "DELETE"])
def business_item(business_id):
    user, error = _require_user(True)
    if error: return error
    business = _owned_business(user["id"], business_id)
    if not business: return _problem("not_found", "Business not found.", 404)
    db = get_db()
    if request.method == "DELETE":
        db.execute("DELETE FROM business_members WHERE business_id = ? AND user_id = ?", (business_id, user["id"]))
        members = db.execute("SELECT COUNT(*) FROM business_members WHERE business_id = ?", (business_id,)).fetchone()[0]
        orders = db.execute("SELECT COUNT(*) FROM orders WHERE business_id = ?", (business_id,)).fetchone()[0]
        if not members and not orders: db.execute("DELETE FROM business_clients WHERE id = ?", (business_id,))
        return "", 204
    payload = _body() or {}; columns, params = [], []
    for key, column, maximum in (("name", "company_name", 200), ("email", "email", 254), ("phone", "phone", 50)):
        if key in payload:
            value = _text(payload[key], maximum, key != "phone")
            if value is None: return _problem("validation_failed", f"Invalid {key}.")
            columns.append(f"{column} = ?"); params.append(value.casefold() if key == "email" else value)
    if columns:
        try: db.execute(f"UPDATE business_clients SET {', '.join(columns)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (*params, business_id))
        except sqlite3.IntegrityError: return _problem("email_exists", "That email is already registered.", 409)
    return jsonify(business=_business_dto(db.execute("SELECT * FROM business_clients WHERE id = ?", (business_id,)).fetchone()))


def _address_input(payload, partial=False):
    fields = (("label", "label", 80), ("line1", "line1", 300), ("line2", "line2", 300), ("city", "city", 100), ("postalCode", "postal_code", 30), ("countryCode", "country_code", 2), ("taxId", "tax_id", 50), ("email", "email", 254), ("phone", "phone", 50))
    result = {}
    for key, column, maximum in fields:
        if key in payload or not partial:
            result[column] = _text(payload.get(key), maximum, key in {"label", "line1", "city", "countryCode", "taxId"})
    if any(value is None for value in result.values()): return None
    if result.get("email") and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", result["email"]): return None
    if "tax_id" in result: result["tax_id"] = result["tax_id"].upper()
    if "email" in result: result["email"] = result["email"].casefold()
    if "isDefault" in payload or not partial: result["is_default"] = 1 if payload.get("isDefault") else 0
    return result


@commerce.post("/businesses/<int:business_id>/addresses")
def address_create(business_id):
    user, error = _require_user(True)
    if error: return error
    if not _owned_business(user["id"], business_id): return _problem("not_found", "Business not found.", 404)
    values = _address_input(_body() or {})
    if values is None: return _problem("validation_failed", "Check the address fields.")
    db = get_db()
    if values["is_default"]: db.execute("UPDATE business_addresses SET is_default = 0 WHERE business_id = ?", (business_id,))
    elif not db.execute("SELECT 1 FROM business_addresses WHERE business_id = ?", (business_id,)).fetchone(): values["is_default"] = 1
    cursor = db.execute(f"INSERT INTO business_addresses(business_id,{','.join(values)}) VALUES (?,{','.join('?' for _ in values)})", (business_id, *values.values()))
    return jsonify(address=_address_dto(db.execute("SELECT * FROM business_addresses WHERE id = ?", (cursor.lastrowid,)).fetchone())), 201


@commerce.route("/businesses/<int:business_id>/addresses/<int:address_id>", methods=["PATCH", "DELETE"])
def address_item(business_id, address_id):
    user, error = _require_user(True)
    if error: return error
    if not _owned_business(user["id"], business_id): return _problem("not_found", "Business not found.", 404)
    db = get_db(); address = db.execute("SELECT * FROM business_addresses WHERE id = ? AND business_id = ?", (address_id, business_id)).fetchone()
    if not address: return _problem("not_found", "Address not found.", 404)
    if request.method == "DELETE":
        db.execute("DELETE FROM business_addresses WHERE id = ?", (address_id,))
        if address["is_default"]:
            replacement = db.execute("SELECT id FROM business_addresses WHERE business_id = ? ORDER BY id LIMIT 1", (business_id,)).fetchone()
            if replacement: db.execute("UPDATE business_addresses SET is_default = 1 WHERE id = ?", (replacement["id"],))
        return "", 204
    values = _address_input(_body() or {}, True)
    if values is None: return _problem("validation_failed", "Check the address fields.")
    if values.get("is_default"): db.execute("UPDATE business_addresses SET is_default = 0 WHERE business_id = ?", (business_id,))
    if values: db.execute(f"UPDATE business_addresses SET {','.join(f'{key}=?' for key in values)}, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*values.values(), address_id))
    return jsonify(address=_address_dto(db.execute("SELECT * FROM business_addresses WHERE id = ?", (address_id,)).fetchone()))


@commerce.get("/catalog/categories")
def catalog_categories():
    invalid = _verified()
    if invalid: return invalid
    return jsonify(categories=[_category_dto(row) for row in get_db().execute("SELECT * FROM categories WHERE active = 1 ORDER BY name_sq")])


@commerce.get("/catalog/brands")
def catalog_brands():
    invalid = _verified()
    if invalid: return invalid
    return jsonify(brands=[_brand_dto(row) for row in get_db().execute("SELECT * FROM brands WHERE active = 1 ORDER BY name")])


@commerce.get("/catalog")
def catalog_bootstrap():
    invalid = _verified()
    if invalid: return invalid
    user = _current_user(); tier = _price_tier(user["id"]) if user else "standard"; db = get_db()
    categories = [_category_dto(row) for row in db.execute("SELECT * FROM categories WHERE active=1 ORDER BY name_sq")]
    brands = [_brand_dto(row) for row in db.execute("SELECT * FROM brands WHERE active=1 ORDER BY name")]
    new = db.execute("SELECT * FROM products WHERE active=1 AND instr(','||badges||',', ',new,')>0 ORDER BY updated_at DESC LIMIT 20").fetchall()
    monthly = db.execute("SELECT * FROM products WHERE active=1 ORDER BY monthly_units_sold DESC, id LIMIT 20").fetchall()
    offers = db.execute("SELECT * FROM products WHERE active=1 AND instr(','||badges||',', ',offer,')>0 ORDER BY id LIMIT 20").fetchall()
    return jsonify(categories=categories, brands=brands, newProducts=[_product_dto(row,tier) for row in new], monthlyProducts=[_product_dto(row,tier) for row in monthly], offers=[_product_dto(row,tier) for row in offers])


@commerce.get("/catalog/products")
def catalog_products():
    invalid = _verified()
    if invalid: return invalid
    user = _current_user(); tier = _price_tier(user["id"]) if user else "standard"
    page = max(1, request.args.get("page", 1, type=int)); size = min(100, max(1, request.args.get("pageSize", 20, type=int)))
    conditions = ["p.active=1"]; params = []
    query = request.args.get("query", "").strip()
    if query: conditions.append("(p.name_sq LIKE ? OR p.name_en LIKE ? OR p.sku LIKE ?)"); params.extend([f"%{query}%"]*3)
    for key, column in (("category", "p.category_id"), ("brand", "p.brand_id")):
        if request.args.get(key): conditions.append(f"{column}=?"); params.append(request.args[key])
    for badge in request.args.getlist("badges"):
        if badge in {"new","offer","monthly_top"}: conditions.append("instr(','||p.badges||',', ?) > 0"); params.append(f",{badge},")
    order = {"price_asc":"p.unit_price_cents ASC", "price_desc":"p.unit_price_cents DESC", "name_asc":"p.name_sq ASC"}.get(request.args.get("sort"), "p.monthly_units_sold DESC, p.id")
    where = " AND ".join(conditions); db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM products p WHERE {where}", params).fetchone()[0]
    rows = db.execute(f"SELECT p.* FROM products p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*params, size, (page-1)*size)).fetchall()
    return jsonify(_page([_product_dto(row,tier) for row in rows], page, size, total))


@commerce.get("/catalog/products/<product_id>")
def catalog_product(product_id):
    invalid = _verified()
    if invalid: return invalid
    product = get_db().execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
    if not product: return _problem("not_found", "Product not found.", 404)
    user = _current_user(); tier = _price_tier(user["id"]) if user else "standard"
    return jsonify(product=_product_dto(product,tier))


@commerce.get("/cart")
def cart_get():
    user, error = _require_user()
    return error or jsonify(_cart_dto(user["id"]))


@commerce.route("/cart", methods=["PUT", "DELETE"])
def cart_replace_or_clear():
    user, error = _require_user(True)
    if error: return error
    if request.method == "DELETE": get_db().execute("DELETE FROM cart_items WHERE user_id=?", (user["id"],)); return "", 204
    result = _replace_cart(user["id"], (_body() or {}).get("items"))
    return jsonify(result) if result is not None else _problem("invalid_items", "One or more cart items are invalid.")


@commerce.route("/cart/items/<product_id>", methods=["PUT", "DELETE"])
def cart_item(product_id):
    user, error = _require_user(True)
    if error: return error
    db = get_db()
    if request.method == "DELETE": db.execute("DELETE FROM cart_items WHERE user_id=? AND product_id=?", (user["id"],product_id)); return jsonify(_cart_dto(user["id"]))
    quantity = (_body() or {}).get("quantity"); validated = _validated_items([{"productId":product_id,"quantity":quantity}])
    if validated is None: return _problem("invalid_item", "Product or quantity is invalid.")
    db.execute("INSERT INTO cart_items(user_id,product_id,quantity) VALUES (?,?,?) ON CONFLICT(user_id,product_id) DO UPDATE SET quantity=excluded.quantity,updated_at=CURRENT_TIMESTAMP", (user["id"],product_id,quantity))
    return jsonify(_cart_dto(user["id"]))


@commerce.route("/saved-lists", methods=["GET", "POST"])
def saved_lists():
    user, error = _require_user(request.method == "POST")
    if error: return error
    db = get_db()
    if request.method == "GET": return jsonify(lists=[_list_dto(row) for row in db.execute("SELECT * FROM saved_lists WHERE user_id=? ORDER BY id DESC", (user["id"],))])
    payload = _body() or {}; name = _text(payload.get("name"),80,True); items = _validated_items(payload.get("items"), allow_empty=True)
    if not name or items is None: return _problem("validation_failed", "A name and valid items are required.")
    db.execute("BEGIN IMMEDIATE")
    try:
        cursor=db.execute("INSERT INTO saved_lists(user_id,name) VALUES (?,?)",(user["id"],name)); db.executemany("INSERT INTO saved_list_items(list_id,product_id,quantity) VALUES (?,?,?)",[(cursor.lastrowid,pid,value[1]) for pid,value in items.items()]); db.execute("COMMIT")
    except Exception: db.execute("ROLLBACK"); raise
    return jsonify(list=_list_dto(db.execute("SELECT * FROM saved_lists WHERE id=?",(cursor.lastrowid,)).fetchone())),201


@commerce.route("/saved-lists/<int:list_id>", methods=["PUT", "DELETE"])
def saved_list_item(list_id):
    user,error=_require_user(True)
    if error:return error
    db=get_db(); row=db.execute("SELECT * FROM saved_lists WHERE id=? AND user_id=?",(list_id,user["id"])).fetchone()
    if not row:return _problem("not_found","Saved list not found.",404)
    if request.method=="DELETE":db.execute("DELETE FROM saved_lists WHERE id=?",(list_id,));return "",204
    payload=_body() or {}; name=_text(payload.get("name"),80,True) if "name" in payload else row["name"]
    items=_validated_items(payload.get("items"), allow_empty=True) if "items" in payload else None
    if not name or ("items" in payload and items is None):return _problem("validation_failed","Invalid list data.")
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("UPDATE saved_lists SET name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(name,list_id))
        if items is not None:db.execute("DELETE FROM saved_list_items WHERE list_id=?",(list_id,));db.executemany("INSERT INTO saved_list_items(list_id,product_id,quantity) VALUES (?,?,?)",[(list_id,pid,value[1]) for pid,value in items.items()])
        db.execute("COMMIT")
    except Exception:db.execute("ROLLBACK");raise
    return jsonify(list=_list_dto(db.execute("SELECT * FROM saved_lists WHERE id=?",(list_id,)).fetchone()))


@commerce.route("/orders", methods=["GET", "POST"])
def orders():
    user,error=_require_user(request.method=="POST")
    if error:return error
    db=get_db()
    if request.method=="GET":
        page=max(1,request.args.get("page",1,type=int));size=20
        owned=[row["id"] for row in _user_businesses(user["id"])]
        if not owned:return jsonify(_page([],page,size,0))
        marks=','.join('?' for _ in owned);total=db.execute(f"SELECT COUNT(*) FROM orders WHERE business_id IN ({marks})",owned).fetchone()[0]
        rows=db.execute(f"SELECT * FROM orders WHERE business_id IN ({marks}) ORDER BY id DESC LIMIT ? OFFSET ?",(*owned,size,(page-1)*size)).fetchall()
        return jsonify(_page([_order_dto(row,user["id"]) for row in rows],page,size,total))
    payload=_body() or {}; business_id=payload.get("businessId");address_id=payload.get("addressId");payment=payload.get("paymentMethod");note=_text(payload.get("note"),1000);raw_items=payload.get("items")
    business=_owned_business(user["id"],business_id,True) if str(business_id).isdigit() else None
    address=db.execute("SELECT * FROM business_addresses WHERE id=? AND business_id=?",(address_id,business_id)).fetchone() if business and str(address_id).isdigit() else None
    if not business or not address or payment not in PAYMENTS or not isinstance(raw_items,list) or not raw_items:return _problem("validation_failed","Business, address, payment method or items are invalid.")
    key=_text(request.headers.get("Idempotency-Key"),100)
    if key:
        existing=db.execute("SELECT * FROM orders WHERE business_id=? AND idempotency_key=?",(business_id,key)).fetchone()
        if existing:return jsonify(order=_order_dto(existing,user["id"]))
    reference="ORD-"+secrets.token_hex(6).upper();db.execute("BEGIN IMMEDIATE")
    try:
        items=_validated_items(raw_items)
        if items is None:
            db.execute("ROLLBACK");return _problem("inventory_unavailable","One or more products do not have enough stock.",409)
        subtotal=vat=0;tier=business["price_tier"];priced=[]
        for product_id,(product,quantity) in items.items():
            unit=_unit_price(product,quantity,tier);line=unit*quantity;subtotal+=line;vat+=round(line*product["vat_basis_points"]/10000);priced.append((product_id,quantity,unit,line))
            if not _adjust_inventory(db,product_id,quantity):
                db.execute("ROLLBACK");return _problem("inventory_unavailable","One or more products do not have enough stock.",409)
        cursor=db.execute("INSERT INTO orders(reference,business_id,submitted_by,status,delivery_address,address_id,payment_method,note,idempotency_key,server_total_cents,subtotal_cents,vat_cents,total_cents,currency) VALUES (?,?,?,'submitted',?,?,?,?,?,?,?,?,?,'ALL')",(reference,business_id,user["id"],address["line1"],address_id,payment,note,key,subtotal+vat,subtotal,vat,subtotal+vat))
        db.executemany("INSERT INTO order_items(order_id,product_id,quantity,unit_price_cents,line_total_cents) VALUES (?,?,?,?,?)",[(cursor.lastrowid,*line) for line in priced])
        db.execute("INSERT INTO order_status_events(order_id,status,changed_by) VALUES (?,'submitted',?)",(cursor.lastrowid,user["id"]))
        db.executemany("UPDATE products SET monthly_units_sold=monthly_units_sold+? WHERE id=?",[(quantity,product_id) for product_id,quantity,_,_ in priced]);db.execute("DELETE FROM cart_items WHERE user_id=?",(user["id"],));db.execute("COMMIT")
    except Exception:db.execute("ROLLBACK");raise
    return jsonify(order=_order_dto(db.execute("SELECT * FROM orders WHERE id=?",(cursor.lastrowid,)).fetchone(),user["id"])),201


@commerce.get("/orders/<int:order_id>")
def order_get(order_id):
    user,error=_require_user()
    if error:return error
    order=get_db().execute("SELECT o.* FROM orders o JOIN business_members m ON m.business_id=o.business_id WHERE o.id=? AND m.user_id=?",(order_id,user["id"])).fetchone()
    return jsonify(order=_order_dto(order,user["id"])) if order else _problem("not_found","Order not found.",404)


@commerce.patch("/orders/<int:order_id>")
def order_update(order_id):
    user,error=_require_user(True)
    if error:return error
    raw_items=(_body() or {}).get("items")
    db=get_db();db.execute("BEGIN IMMEDIATE")
    try:
        order=db.execute(
            "SELECT o.*,b.price_tier FROM orders o JOIN business_members m ON m.business_id=o.business_id "
            "JOIN business_clients b ON b.id=o.business_id WHERE o.id=? AND m.user_id=?",
            (order_id,user["id"]),
        ).fetchone()
        if not order:
            db.execute("ROLLBACK");return _problem("not_found","Order not found.",404)
        if order["status"] not in {"submitted","confirmed","processing"}:
            db.execute("ROLLBACK");return _problem("order_locked","This order has already been shipped or closed and can no longer be changed.",409)
        pending=db.execute("SELECT id FROM order_change_requests WHERE order_id=? AND status='pending'",(order_id,)).fetchone()
        if pending:
            db.execute("ROLLBACK");return _problem("order_change_pending","This order already has a change waiting for packing review.",409)
        previous={row["product_id"]:row["quantity"] for row in db.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?",(order_id,))}
        items=_validated_items(raw_items, stock_credit=previous)
        if items is None:
            db.execute("ROLLBACK");return _problem("inventory_unavailable","One or more products do not have enough stock.",409)
        subtotal=vat=0;priced=[]
        for product_id,(product,quantity) in items.items():
            unit=_unit_price(product,quantity,order["price_tier"]);line=unit*quantity;subtotal+=line;vat+=round(line*product["vat_basis_points"]/10000);priced.append((product_id,quantity,unit,line))
        cursor=db.execute(
            "INSERT INTO order_change_requests(order_id,requested_by,status,subtotal_cents,vat_cents,total_cents,currency) "
            "VALUES (?,?,'pending',?,?,?,'ALL')",(order_id,user["id"],subtotal,vat,subtotal+vat)
        )
        db.executemany(
            "INSERT INTO order_change_request_items(request_id,product_id,quantity,unit_price_cents,line_total_cents) VALUES (?,?,?,?,?)",
            [(cursor.lastrowid,*line) for line in priced],
        )
        db.execute("UPDATE orders SET updated_at=CURRENT_TIMESTAMP WHERE id=?",(order_id,))
        db.execute("INSERT INTO order_status_events(order_id,status,changed_by) VALUES (?,?,?)",(order_id,order["status"],user["id"]))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK");raise
    return jsonify(order=_order_dto(db.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone(),user["id"])),202


@commerce.get("/orders/updates")
def order_updates():
    user,error=_require_user()
    if error:return error
    after=max(0,request.args.get("afterEventId",0,type=int));db=get_db()
    rows=db.execute(
        "SELECT e.id,e.order_id,e.status,e.created_at,o.reference FROM order_status_events e "
        "JOIN orders o ON o.id=e.order_id JOIN business_members m ON m.business_id=o.business_id "
        "WHERE m.user_id=? AND e.id>? ORDER BY e.id ASC LIMIT 100",(user["id"],after)
    ).fetchall()
    latest=db.execute(
        "SELECT COALESCE(MAX(e.id),0) FROM order_status_events e JOIN orders o ON o.id=e.order_id "
        "JOIN business_members m ON m.business_id=o.business_id WHERE m.user_id=?",(user["id"],)
    ).fetchone()[0]
    next_id=rows[-1]["id"] if rows else after
    return jsonify(
        changes=[{"eventId":row["id"],"orderId":str(row["order_id"]),"reference":row["reference"],"status":row["status"],"changedAt":row["created_at"]} for row in rows],
        nextEventId=next_id,latestEventId=latest,hasMore=next_id<latest,
    )


@commerce.post("/orders/<int:order_id>/reorder")
def order_reorder(order_id):
    user,error=_require_user(True)
    if error:return error
    order=get_db().execute("SELECT o.id FROM orders o JOIN business_members m ON m.business_id=o.business_id WHERE o.id=? AND m.user_id=?",(order_id,user["id"])).fetchone()
    if not order:return _problem("not_found","Order not found.",404)
    items=[{"productId":row["product_id"],"quantity":row["quantity"]} for row in get_db().execute("SELECT product_id,quantity FROM order_items WHERE order_id=?",(order_id,))]
    result=_replace_cart(user["id"],items)
    return jsonify(result) if result is not None else _problem("unavailable_items","Some products can no longer be ordered.",409)


@commerce.route("/quotes", methods=["GET", "POST"])
def quotes():
    user,error=_require_user(request.method=="POST")
    if error:return error
    db=get_db()
    if request.method=="GET":
        page=max(1,request.args.get("page",1,type=int));size=20;owned=[row["id"] for row in _user_businesses(user["id"])]
        if not owned:return jsonify(_page([],page,size,0))
        marks=','.join('?' for _ in owned);total=db.execute(f"SELECT COUNT(*) FROM quotes WHERE business_id IN ({marks})",owned).fetchone()[0];rows=db.execute(f"SELECT * FROM quotes WHERE business_id IN ({marks}) ORDER BY id DESC LIMIT ? OFFSET ?",(*owned,size,(page-1)*size)).fetchall()
        return jsonify(_page([_quote_dto(row,user["id"]) for row in rows],page,size,total))
    payload=_body() or {};business_id=payload.get("businessId");address_id=payload.get("addressId");note=_text(payload.get("note"),1000);business=_owned_business(user["id"],business_id,True) if str(business_id).isdigit() else None;address=db.execute("SELECT 1 FROM business_addresses WHERE id=? AND business_id=?",(address_id,business_id)).fetchone() if business and str(address_id).isdigit() else None;items=_validated_items(payload.get("items"))
    if not business or not address or items is None:return _problem("validation_failed","Business, address or items are invalid.")
    key=_text(request.headers.get("Idempotency-Key"),100)
    if key:
        existing=db.execute("SELECT * FROM quotes WHERE business_id=? AND idempotency_key=?",(business_id,key)).fetchone()
        if existing:return jsonify(quote=_quote_dto(existing,user["id"]))
    tier=business["price_tier"];priced=[];total=0
    for pid,(product,qty) in items.items():unit=_unit_price(product,qty,tier);line=unit*qty;total+=line+round(line*product["vat_basis_points"]/10000);priced.append((pid,qty,unit,line))
    reference="QTE-"+secrets.token_hex(6).upper();db.execute("BEGIN IMMEDIATE")
    try:
        cursor=db.execute("INSERT INTO quotes(reference,business_id,address_id,submitted_by,status,note,estimated_total_cents,idempotency_key,currency) VALUES (?,?,?,?,'submitted',?,?,?,'ALL')",(reference,business_id,address_id,user["id"],note,total,key));db.executemany("INSERT INTO quote_items(quote_id,product_id,quantity,unit_price_cents,line_total_cents) VALUES (?,?,?,?,?)",[(cursor.lastrowid,*line) for line in priced]);db.execute("COMMIT")
    except Exception:db.execute("ROLLBACK");raise
    return jsonify(quote=_quote_dto(db.execute("SELECT * FROM quotes WHERE id=?",(cursor.lastrowid,)).fetchone(),user["id"])),201


@commerce.get("/quotes/<int:quote_id>")
def quote_get(quote_id):
    user,error=_require_user()
    if error:return error
    quote=get_db().execute("SELECT q.* FROM quotes q JOIN business_members m ON m.business_id=q.business_id WHERE q.id=? AND m.user_id=?",(quote_id,user["id"])).fetchone()
    return jsonify(quote=_quote_dto(quote,user["id"])) if quote else _problem("not_found","Quote not found.",404)


@commerce.get("/delivery/options")
def delivery_options():
    user,error=_require_user()
    if error:return error
    business_id=request.args.get("businessId");address_id=request.args.get("addressId");business=_owned_business(user["id"],business_id,True) if str(business_id).isdigit() else None
    address=get_db().execute("SELECT * FROM business_addresses WHERE id=? AND business_id=?",(address_id,business_id)).fetchone() if business and str(address_id).isdigit() else None
    if not address:return _problem("not_found","Address not found.",404)
    rows=get_db().execute("SELECT * FROM delivery_options WHERE (business_id=? OR business_id IS NULL) AND (address_id=? OR address_id IS NULL) ORDER BY id",(business_id,address_id)).fetchall()
    if rows:
        options=[{"id":str(row["id"]),"businessId":str(business_id),"addressId":str(address_id),"serviceArea":row["service_area"],"nextAvailableDate":row["next_available_date"],"cutoffTime":row["cutoff_time"],"schedule":json.loads(row["schedule_json"]),"available":bool(row["available"]),"message":_localized(row["message_sq"],row["message_en"])} for row in rows]
    else:
        options=[{"id":"standard","businessId":str(business_id),"addressId":str(address_id),"serviceArea":address["city"] or "Albania","nextAvailableDate":(date.today()+timedelta(days=1)).isoformat(),"cutoffTime":"14:00","schedule":["Monday","Tuesday","Wednesday","Thursday","Friday"],"available":True,"message":_localized("Dorëzimi konfirmohet nga ekipi ynë.","Delivery is confirmed by our team.")}]
    return jsonify(options=options)


@commerce.route("/comparison", methods=["GET", "PUT"])
def comparison():
    user,error=_require_user(request.method=="PUT")
    if error:return error
    db=get_db()
    if request.method=="GET":return jsonify(productIds=[row["product_id"] for row in db.execute("SELECT product_id FROM comparisons WHERE user_id=? ORDER BY position",(user["id"],))])
    ids=(_body() or {}).get("productIds")
    if not isinstance(ids,list) or len(ids)>4 or len(set(ids))!=len(ids):return _problem("validation_failed","Select up to four unique products.")
    if ids:
        found=db.execute(f"SELECT COUNT(*) FROM products WHERE active=1 AND id IN ({','.join('?' for _ in ids)})",ids).fetchone()[0]
        if found!=len(ids):return _problem("invalid_product","One or more products are invalid.")
    db.execute("BEGIN IMMEDIATE")
    try:db.execute("DELETE FROM comparisons WHERE user_id=?",(user["id"],));db.executemany("INSERT INTO comparisons(user_id,product_id,position) VALUES (?,?,?)",[(user["id"],pid,index) for index,pid in enumerate(ids)]);db.execute("COMMIT")
    except Exception:db.execute("ROLLBACK");raise
    return jsonify(productIds=ids)


@commerce.get("/storefront/bootstrap")
def storefront_bootstrap():
    user,error=_require_user()
    if error:return error
    lists=[_list_dto(row) for row in get_db().execute("SELECT * FROM saved_lists WHERE user_id=? ORDER BY id DESC",(user["id"],))]
    compared=[row["product_id"] for row in get_db().execute("SELECT product_id FROM comparisons WHERE user_id=? ORDER BY position",(user["id"],))]
    return jsonify(account=_account_dto(user),cart=_cart_dto(user["id"]),savedLists=lists,comparedProductIds=compared,contact={"email":"info@dukagroup.al","phone":"+355 44 540 566","address":"Vorë, Tiranë, Albania"})
