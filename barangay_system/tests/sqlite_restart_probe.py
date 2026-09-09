"""Separate-process read probe used by validate_sqlite.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app
from models import (
    ActivityLog,
    BlotterCase,
    EventRequest,
    Notification,
    PermitApplication,
    Report,
    Schedule,
    SystemSetting,
    User,
    db,
)


if len(sys.argv) != 10:
    raise SystemExit("Expected nine record IDs")

ids = list(map(int, sys.argv[1:]))
models = (User, PermitApplication, EventRequest, BlotterCase, Schedule, Notification, ActivityLog, Report, SystemSetting)

with app.app_context():
    assert db.engine.dialect.name == "sqlite"
    for model, record_id in zip(models, ids):
        assert db.session.get(model, record_id) is not None, f"Missing {model.__name__} {record_id}"
    print("SQLITE_RESTART_PROBE_OK")
