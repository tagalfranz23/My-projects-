import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _absolute_path(value, default):
    """Return a stable absolute path for local and Render deployments."""
    path = os.path.expanduser(value or default)
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return os.path.abspath(path)


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "development-only-change-me")
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    # SQLite is the sole operational database. On Render, set DATA_DIR to the
    # mounted persistent disk (recommended: /var/data). A database kept in the
    # normal Render source directory would be erased on restart or redeploy.
    DATA_DIR = _absolute_path(os.environ.get("DATA_DIR"), os.path.join(BASE_DIR, "instance"))
    SQLITE_DB_PATH = _absolute_path(
        os.environ.get("SQLITE_DB_PATH"), os.path.join(DATA_DIR, "minante.db")
    )
    _DATABASE_URL = os.environ.get("DATABASE_URL")
    if _DATABASE_URL and not _DATABASE_URL.lower().startswith("sqlite:"):
        raise RuntimeError("DATABASE_URL must use SQLite for this deployment.")
    SQLALCHEMY_DATABASE_URI = _DATABASE_URL or (
        "sqlite:///" + SQLITE_DB_PATH.replace("\\", "/")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"timeout": 30, "check_same_thread": False},
        "pool_pre_ping": True,
    }

    # Uploaded evidence must live beside the SQLite file on the persistent disk.
    UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
    PERMIT_FOLDER = os.path.join(DATA_DIR, "permits")

    SESSION_COOKIE_SECURE = _as_bool(os.environ.get("SESSION_COOKIE_SECURE"), False)
    PREFERRED_URL_SCHEME = "https" if SESSION_COOKIE_SECURE else "http"

    # --- SMS Gateway (simulated) ---------------------------------------
    # Swap send_sms() in services/notify.py with a real provider such as
    # Semaphore, Movider, or Twilio when going to production.
    SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "simulated")

    BARANGAY_NAME = os.environ.get("BARANGAY_NAME", "Barangay Minante 1")
    CITY_MUNICIPALITY = os.environ.get("CITY_MUNICIPALITY", "Cauayan City, Isabela")
