import os
import secrets
import threading

from flask import Flask

from . import db
from .config import get_config


def load_dotenv(path=".env"):
    """Minimal .env loader so we do not need python-dotenv."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


def create_app(test_config=None):
    if not (test_config or {}).get("TESTING"):
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
    cfg = get_config()
    app = Flask(__name__)
    if test_config:
        app.config.update(test_config)
    app.config["MAX_CONTENT_LENGTH"] = (cfg.max_audio_mb + 5) * 1024 * 1024
    db.init_db()

    from .routes import bp
    app.register_blueprint(bp)
    from .management import bp as management_bp
    app.config["SETTINGS_ENV_PATH"] = os.path.join(app.root_path, "..", ".env")
    app.config["MANAGEMENT_TOKEN"] = secrets.token_urlsafe(32)
    app.config["MANAGEMENT_LOCK"] = threading.Lock()
    app.config["CONNECTION_TEST_AFTER"] = 0.0
    app.register_blueprint(management_bp)
    from .pets import bp as pets_bp
    app.register_blueprint(pets_bp)

    @app.after_request
    def no_store(resp):
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    return app
