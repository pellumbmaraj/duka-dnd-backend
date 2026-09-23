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
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=16 * 1024,
        JSON_SORT_KEYS=False,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("Set FLASK_SECRET_KEY to a long, random secret before starting the backend.")
    if not app.config.get("TESTING") and len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("FLASK_SECRET_KEY must contain at least 32 characters.")

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    from .auth import auth
    from .database import init_app as init_database
    from .views import views

    init_database(app)
    app.register_blueprint(views)
    app.register_blueprint(auth, url_prefix="/api/v1/auth")

    @app.after_request
    def security_headers(response):
        origin = request.headers.get("Origin")
        if origin and origin in app.config["FRONTEND_ORIGINS"]:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers.add("Vary", "Origin")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error="Request too large."), 413

    return app
