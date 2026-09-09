"""Destructive-safe SQLite integration validation; temporary rows are removed."""
from datetime import date, datetime, timedelta
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import app
from migration import upgrade_legacy_schema
from models import (
    ActivityLog,
    BlotterCase,
    EventRequest,
    Notification,
    PasswordResetToken,
    PermitApplication,
    PermitType,
    Report,
    Schedule,
    SystemSetting,
    User,
    db,
)


PREFIX = "__sqlite_validation__"
PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))


with app.app_context():
    assert db.engine.dialect.name == "sqlite"
    upgrade_legacy_schema()
    assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
    assert db.session.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
    assert db.session.execute(text("PRAGMA busy_timeout")).scalar() >= 30000
    assert db.session.execute(text("PRAGMA integrity_check")).scalar().lower() == "ok"

    def cleanup(user_id=None, shared=True):
        if user_id:
            PermitApplication.query.filter(
                (PermitApplication.applicant_id != user_id)
                & ((PermitApplication.decided_by == user_id) | (PermitApplication.reviewed_by == user_id))
            ).update({"decided_by": None, "reviewed_by": None}, synchronize_session=False)
            EventRequest.query.filter(
                (EventRequest.requester_id != user_id) & (EventRequest.reviewed_by == user_id)
            ).update({"reviewed_by": None}, synchronize_session=False)
            BlotterCase.query.filter(
                (BlotterCase.complainant_id != user_id) & (BlotterCase.updated_by == user_id)
            ).update({"updated_by": None}, synchronize_session=False)
            Schedule.query.filter(
                (Schedule.requested_by != user_id) & (Schedule.confirmed_by == user_id)
            ).update({"confirmed_by": None}, synchronize_session=False)
            Notification.query.filter(
                (Notification.user_id == user_id) | (Notification.recipient_user_id == user_id)
            ).delete(synchronize_session=False)
            Schedule.query.filter_by(requested_by=user_id).delete(synchronize_session=False)
            EventRequest.query.filter_by(requester_id=user_id).delete(synchronize_session=False)
            BlotterCase.query.filter_by(complainant_id=user_id).delete(synchronize_session=False)
            PermitApplication.query.filter_by(applicant_id=user_id).delete(synchronize_session=False)
            PasswordResetToken.query.filter_by(user_id=user_id).delete(synchronize_session=False)
            ActivityLog.query.filter_by(user_id=user_id).delete(synchronize_session=False)
            User.query.filter_by(id=user_id).delete(synchronize_session=False)
        if shared:
            Report.query.filter_by(report_type="validation").delete(synchronize_session=False)
            SystemSetting.query.filter_by(key=PREFIX).delete(synchronize_session=False)
        db.session.commit()

    stale = User.query.filter_by(username=PREFIX).first()
    if stale:
        cleanup(stale.id)

    admin = User.query.filter_by(role="admin", is_active=True).order_by(User.id).first()
    assert admin and admin.password_hash
    admin_id = admin.id
    admin_hash = admin.password_hash

    user = User(
        username=PREFIX,
        email=f"{PREFIX}@local.invalid",
        full_name="Temporary Validation User",
        role="resident",
    )
    user.set_password("ValidationPass123")
    db.session.add(user)
    db.session.flush()
    ptype = PermitType.query.first()
    assert ptype
    permit = PermitApplication(applicant_id=user.id, permit_type_id=ptype.id, purpose="Persistence validation")
    event = EventRequest(
        requester_id=user.id,
        event_name="Validation Event",
        event_type="Community Event",
        description="Temporary persistence test",
        proposed_date=date.today() + timedelta(days=2),
        proposed_location="Barangay Hall",
        expected_attendees=2,
    )
    blotter = BlotterCase(
        complainant_id=user.id,
        respondent_name="Temporary Party",
        incident_type="Validation",
        incident_date=datetime.utcnow(),
        incident_location="Barangay Hall",
        narrative="Temporary persistence test",
    )
    db.session.add_all([permit, event, blotter])
    db.session.flush()
    schedule = Schedule(
        related_type="permit",
        related_id=permit.id,
        requested_by=user.id,
        scheduled_datetime=datetime.utcnow() + timedelta(days=1),
        purpose="Validation",
    )
    notification = Notification(
        user_id=user.id,
        recipient_user_id=user.id,
        title="Validation",
        message="Temporary",
        channel="in_app",
        status="sent",
    )
    activity = ActivityLog(
        user_id=user.id,
        actor_role="resident",
        action="SQLITE_VALIDATION",
        resource="system",
        target_reference=permit.reference_no,
    )
    report = Report(generated_by=admin.id, report_type="validation", format="csv")
    setting = SystemSetting(key=PREFIX, value="created")
    db.session.add_all([schedule, notification, activity, report, setting])
    db.session.commit()
    ids = [user.id, permit.id, event.id, blotter.id, schedule.id, notification.id, activity.id, report.id, setting.id]

    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as current_session:
        csrf = current_session["csrf_token"]
    response = client.post(
        "/login",
        data={"csrf_token": csrf, "username": PREFIX, "password": "ValidationPass123"},
    )
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")
    assert client.get("/healthz").status_code == 200
    receipt_response = client.get(f"/receipts/permit/{permit.id}.pdf")
    assert receipt_response.status_code == 200 and receipt_response.data.startswith(b"%PDF")

    probe = subprocess.run(
        [sys.executable, os.path.join("tests", "sqlite_restart_probe.py"), *map(str, ids)],
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "SQLITE_RESTART_PROBE_OK" in probe.stdout

    try:
        db.session.add(
            PermitApplication(applicant_id=999999999, permit_type_id=ptype.id, purpose="Must fail")
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
    else:
        raise AssertionError("SQLite foreign-key violation was not enforced")

    cleanup(ids[0])
    assert User.query.filter_by(username=PREFIX).first() is None
    preserved_admin = db.session.get(User, admin_id)
    assert preserved_admin and preserved_admin.password_hash == admin_hash
    assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []
    print("SQLITE_VALIDATION_OK: pragmas, login, all stores, process restart, FK rollback, cleanup")
