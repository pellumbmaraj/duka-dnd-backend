import os
from pathlib import Path
from datetime import timedelta

from flask import Flask, jsonify, request


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
        DATABASE=os.environ.get("METRO_DATABASE", str(Path(app.instance_path) / "metro.sqlite3")),
        FRONTEND_ORIGINS={
            origin.strip()
            for origin in os.environ.get("METRO_FRONTEND_ORIGINS", "http://localhost:3000").split(",")
            if origin.strip()
        },
        SESSION_COOKIE_NAME="metro_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.environ.get("METRO_ENV", "development").lower() == "production",
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=16 * 1024,
        JSON_SORT_KEYS=False,
        SMTP_HOST=os.environ.get("SMTP_HOST", ""),
        SMTP_PORT=int(os.environ.get("SMTP_PORT", "587")),
        SMTP_USERNAME=os.environ.get("SMTP_USERNAME", ""),
        SMTP_PASSWORD=os.environ.get("SMTP_PASSWORD", ""),
        SMTP_USE_TLS=os.environ.get("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"},
        SMTP_USE_SSL=os.environ.get("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"},
        MAIL_FROM=os.environ.get("MAIL_FROM", "accounts@dukagroup.al"),
        MAIL_FROM_NAME=os.environ.get("MAIL_FROM_NAME", "DUKA Group"),
        ADMIN_NOTIFICATION_EMAILS=os.environ.get("ADMIN_NOTIFICATION_EMAILS", "admin@dukagroup.al"),
        FRONTEND_PUBLIC_URL=os.environ.get("FRONTEND_PUBLIC_URL", "http://localhost:3000").rstrip("/"),
        ACTIVATION_OTP_MINUTES=int(os.environ.get("ACTIVATION_OTP_MINUTES", "30")),
        MAIL_SUPPRESS_SEND=False,
        PACKING_STAFF_EMAIL=os.environ.get("PACKING_STAFF_EMAIL", "packing@dukagroup.al"),
        PACKING_STAFF_PASSWORD=os.environ.get("PACKING_STAFF_PASSWORD", ""),
        DELIVERY_STAFF_EMAIL=os.environ.get("DELIVERY_STAFF_EMAIL", "delivery@dukagroup.al"),
        DELIVERY_STAFF_PASSWORD=os.environ.get("DELIVERY_STAFF_PASSWORD", ""),
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("Set FLASK_SECRET_KEY to a long, random secret before starting the backend.")
    if not app.config.get("TESTING") and len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("FLASK_SECRET_KEY must contain at least 32 characters.")

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    from .auth import auth
    from .commerce import commerce
    from .database import init_app as init_database
    from .admin import admin
    from .views import views
    from .notifications import record_storefront_mutation

    init_database(app)
    app.register_blueprint(views)
    app.register_blueprint(auth, url_prefix="/api/v1/auth")
    app.register_blueprint(commerce, url_prefix="/api/v1")
    app.register_blueprint(admin)

    @app.after_request
    def notify_administrators(response):
        try:
            record_storefront_mutation(response)
        except Exception:
            app.logger.exception("Could not record the admin notification")
        return response

    @app.after_request
    def security_headers(response):
        origin = request.headers.get("Origin")
        if origin and origin in app.config["FRONTEND_ORIGINS"]:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token, Idempotency-Key"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, PUT, DELETE, OPTIONS"
            response.headers.add("Vary", "Origin")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error="Request too large."), 413

    return app
