from flask import Flask, jsonify, request, send_from_directory
from pydantic import ValidationError

from . import agent, data, db
from .config import MAX_UPLOAD, ROOT
from .flask_routes import HTTPException, bp


def create_app():
    app = Flask(
        __name__, static_folder=str(ROOT / "frontend/dist/assets"), static_url_path="/assets"
    )
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_UPLOAD + 1024 * 1024,
        JSON_AS_ASCII=False,
        TRUSTED_HOSTS=["127.0.0.1", "localhost", "testserver"],
    )
    app.json.ensure_ascii = False
    db.initialize()
    app.register_blueprint(bp)

    @app.before_request
    def check_origin():
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("Origin"):
            if request.headers["Origin"] not in {
                request.host_url.rstrip("/"),
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            }:
                return jsonify(detail="请求来源不受信任。"), 403

    @app.errorhandler(HTTPException)
    def api_error(error):
        return jsonify(detail=error.detail), error.status_code

    @app.errorhandler(ValidationError)
    def validation_error(error):
        return jsonify(detail=str(error)), 422

    @app.errorhandler(data.DataError)
    def data_error(error):
        return jsonify(detail=str(error)), 422

    @app.errorhandler(agent.ModelError)
    def model_error(error):
        return jsonify(detail=str(error)), 502

    @app.errorhandler(KeyError)
    def missing(error):
        return jsonify(detail="记录不存在。"), 404

    @app.get("/")
    def index():
        return send_from_directory(ROOT / "frontend/dist", "index.html")

    return app
