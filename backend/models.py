from flask import Blueprint

models = Blueprint("models", __name__)

@models.route("/model")
def models():
    return "Model"