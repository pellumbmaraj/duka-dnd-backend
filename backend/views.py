from flask import Blueprint, jsonify, redirect, url_for

views = Blueprint("views", __name__)


@views.get("/")
def index():
    return redirect(url_for("admin.dashboard"))


@views.get("/health")
def health():
    return jsonify(status="ok")
