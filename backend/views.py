from flask import Blueprint, jsonify

views = Blueprint("views", __name__)


@views.get("/health")
def health():
    return jsonify(status="ok")
