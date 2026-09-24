import hashlib
import hmac
import secrets
import time
import re

import click
from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .database import get_db

auth = Blueprint("auth", __name__)
# Performs a password hash check for unknown accounts to reduce timing-based enumeration.
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32), method="scrypt")
_RATE_WINDOW_SECONDS = 60
_RATE_LIMIT = 10


@auth.cli.command("create-user")
@click.argument("email")
@click.option("--role", type=click.Choice(["customer", "staff", "admin"]), default="customer", show_default=True)
@click.option("--name", "display_name", prompt="Display name")
def create_user(email, role, display_name):
    """Provision an account from a trusted command line, never from a public route."""
    email = email.strip().casefold()
    display_name = display_name.strip()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise click.ClickException("Enter a valid email address.")
    if not display_name or len(display_name) > 100:
        raise click.ClickException("Display name must be between 1 and 100 characters.")
    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    if len(password) < 12 or len(password) > 256:
        raise click.ClickException("Password must be between 12 and 256 characters.")
    try:
        get_db().execute(
            "INSERT INTO users(email, password_hash, display_name, role) VALUES (?, ?, ?, ?)",
            (email, generate_password_hash(password, method="scrypt"), display_name, role),
        )
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            raise click.ClickException("An account with that email already exists.") from error
        raise
    click.echo(f"Created {role} account for {email}.")


def _json_body():
    if not request.is_json:
        return None
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else None


def _valid_origin():
    origin = request.headers.get("Origin")
    return bool(origin and origin in current_app.config["FRONTEND_ORIGINS"])


def _csrf_ok():
    submitted = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    return bool(submitted and expected and hmac.compare_digest(submitted, expected))


def _rate_key(value):
    return hmac.new(
        current_app.config["SECRET_KEY"].encode("utf-8"),
        value.encode("utf-8", "replace"),
        hashlib.sha256,
    ).hexdigest()


def _rate_limited(email):
    now = int(time.time())
    db = get_db()
    # Store only keyed digests; raw client IP addresses and emails are not persisted here.
    identities = {
        _rate_key("ip:" + request.remote_addr if request.remote_addr else "ip:unknown"),
        _rate_key("email:" + email.casefold()),
    }
    db.execute("BEGIN IMMEDIATE")
    try:
        limited = False
        for key in identities:
            row = db.execute("SELECT window_started, attempts FROM auth_attempts WHERE bucket_key = ?", (key,)).fetchone()
            if row and now - row["window_started"] < _RATE_WINDOW_SECONDS and row["attempts"] >= _RATE_LIMIT:
                limited = True
        for key in identities:
            row = db.execute("SELECT window_started, attempts FROM auth_attempts WHERE bucket_key = ?", (key,)).fetchone()
            if row and now - row["window_started"] < _RATE_WINDOW_SECONDS:
                db.execute("UPDATE auth_attempts SET attempts = attempts + 1 WHERE bucket_key = ?", (key,))
            else:
                db.execute(
                    "INSERT INTO auth_attempts(bucket_key, window_started, attempts) VALUES (?, ?, 1) "
                    "ON CONFLICT(bucket_key) DO UPDATE SET window_started = excluded.window_started, attempts = 1",
                    (key, now),
                )
        db.execute("DELETE FROM auth_attempts WHERE window_started < ?", (now - 86400,))
        db.execute("COMMIT")
        return limited
    except Exception:
        db.execute("ROLLBACK")
        raise


def _clear_successful_login_attempts(email):
    """A valid login must not consume the failed-attempt allowance."""
    identities = {
        _rate_key("ip:" + request.remote_addr if request.remote_addr else "ip:unknown"),
        _rate_key("email:" + email.casefold()),
    }
    get_db().executemany("DELETE FROM auth_attempts WHERE bucket_key = ?", [(key,) for key in identities])


def _user_payload(user):
    return {
        "id": user["id"],
        "email": user["email"],
        "displayName": user["display_name"],
        "role": user["role"],
        "mustChangePassword": bool(user["must_change_password"]),
    }


@auth.route("/csrf", methods=["GET", "OPTIONS"])
def csrf_token():
    if request.method == "OPTIONS":
        return "", 204
    if not _valid_origin():
        return jsonify(error="Request origin is not allowed."), 403
    token = secrets.token_urlsafe(32)
    session["csrf_token"] = token
    return jsonify(csrfToken=token)


@auth.route("/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return "", 204
    if not _valid_origin() or not _csrf_ok():
        return jsonify(error="Request could not be verified."), 403
    payload = _json_body()
    if payload is None:
        return jsonify(error="Invalid request."), 400

    email = payload.get("email")
    password = payload.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify(error="Invalid email or password."), 400
    email = email.strip().casefold()
    if not email or len(email) > 254 or len(password) > 256 or not password:
        return jsonify(error="Invalid email or password."), 400

    if _rate_limited(email):
        return jsonify(error="Too many sign-in attempts. Try again shortly."), 429, {"Retry-After": str(_RATE_WINDOW_SECONDS)}

    db = get_db()
    user = db.execute(
        "SELECT id, email, password_hash, display_name, role, is_active, auth_version, must_change_password "
        "FROM users WHERE email = ? COLLATE NOCASE",
        (email,),
    ).fetchone()
    candidate_hash = user["password_hash"] if user else _DUMMY_HASH
    password_matches = check_password_hash(candidate_hash, password)
    db.execute("DELETE FROM account_activation_otps WHERE expires_at <= CURRENT_TIMESTAMP")
    activation = db.execute(
        "SELECT otp_hash, attempts FROM account_activation_otps "
        "WHERE user_id = ? AND expires_at > CURRENT_TIMESTAMP",
        (user["id"] if user else -1,),
    ).fetchone()
    otp_matches = check_password_hash(activation["otp_hash"] if activation else _DUMMY_HASH, password)
    using_otp = bool(user and user["is_active"] and activation and activation["attempts"] < 5 and otp_matches)
    if not user or not user["is_active"] or (not password_matches and not using_otp):
        if user and activation:
            db.execute(
                "UPDATE account_activation_otps SET attempts = attempts + 1 WHERE user_id = ?",
                (user["id"],),
            )
            db.execute(
                "DELETE FROM account_activation_otps WHERE user_id = ? AND attempts >= 5",
                (user["id"],),
            )
        return jsonify(error="Invalid email or password."), 401

    _clear_successful_login_attempts(email)

    # Clear any pre-auth session state to prevent session fixation.
    if using_otp:
        db.execute("DELETE FROM account_activation_otps WHERE user_id = ?", (user["id"],))
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["auth_version"] = user["auth_version"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    if using_otp:
        session["initial_password_required"] = True
    return jsonify(user=_user_payload(user), csrfToken=session["csrf_token"]), 200


@auth.route("/complete-activation", methods=["POST", "OPTIONS"])
def complete_activation():
    if request.method == "OPTIONS":
        return "", 204
    if not _valid_origin() or not _csrf_ok():
        return jsonify(error={"code": "csrf_failed", "message": "Request could not be verified."}), 403
    user_id = session.get("user_id")
    if not user_id or not session.get("initial_password_required"):
        return jsonify(error={"code": "activation_required", "message": "Use your one-time code first."}), 401
    payload = _json_body() or {}
    password = payload.get("newPassword")
    if not isinstance(password, str) or not 12 <= len(password) <= 256 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return jsonify(error={"code": "weak_password", "message": "Use at least 12 characters with a letter and a number."}), 400
    db = get_db()
    user = db.execute(
        "SELECT id, is_active, auth_version, must_change_password FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not user or not user["is_active"] or not user["must_change_password"]:
        session.clear()
        return jsonify(error={"code": "activation_required", "message": "Account activation is no longer available."}), 401
    password_hash = generate_password_hash(password, method="scrypt")
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, auth_version = auth_version + 1 WHERE id = ?",
            (password_hash, user_id),
        )
        db.execute("DELETE FROM account_activation_otps WHERE user_id = ?", (user_id,))
        saved_user = db.execute(
            "SELECT id, email, password_hash, display_name, role, is_active, auth_version, must_change_password "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not saved_user or not check_password_hash(saved_user["password_hash"], password):
            raise RuntimeError("The permanent password could not be verified after saving.")
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    session["auth_version"] = saved_user["auth_version"]
    session.pop("initial_password_required", None)
    return jsonify(message="Password created. Your account is ready.", user=_user_payload(saved_user)), 200


@auth.route("/me", methods=["GET", "OPTIONS"])
def current_user():
    if request.method == "OPTIONS":
        return "", 204
    user_id = session.get("user_id")
    if not user_id:
        return jsonify(error="Authentication required."), 401
    user = get_db().execute(
        "SELECT id, email, display_name, role, is_active, auth_version, must_change_password FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not user or not user["is_active"] or user["auth_version"] != session.get("auth_version"):
        session.clear()
        return jsonify(error="Authentication required."), 401
    return jsonify(user=_user_payload(user))


@auth.route("/logout", methods=["POST", "OPTIONS"])
def logout():
    if request.method == "OPTIONS":
        return "", 204
    if not _valid_origin() or not _csrf_ok():
        return jsonify(error="Request could not be verified."), 403
    session.clear()
    response = current_app.make_response((jsonify(message="Signed out."), 200))
    response.delete_cookie(
        current_app.config["SESSION_COOKIE_NAME"],
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response
