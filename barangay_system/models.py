"""Database models for the Barangay Minante 1 management system.

Legacy event columns remain in storage so upgrades preserve existing history.
New workflows use configured categories, venues, fee snapshots and reservations.
"""

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, event
from sqlalchemy.engine import Engine
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def configure_sqlite_connection(dbapi_connection, _connection_record):
    """Enable integrity and safer concurrent access for the SQLite fallback."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def utcnow():
    return datetime.utcnow()


def make_reference(prefix):
    return f"{prefix}-{utcnow().year}-{secrets.randbelow(1000000):06d}"


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class User(db.Model, TimestampMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(
        db.String(20), nullable=False, default="resident", index=True
    )
    full_name = db.Column(db.String(150), nullable=False)
    contact_number = db.Column(db.String(20))
    email = db.Column(db.String(150), unique=True, index=True)
    address = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    resident_profile = db.relationship(
        "ResidentProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="ResidentProfile.user_id",
    )

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)


class ResidentType(db.Model, TimestampMixin):
    """Administrator-managed residency classifications."""

    __tablename__ = "resident_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)


class AcceptedIdType(db.Model, TimestampMixin):
    """Administrator-managed identity documents accepted at registration."""

    __tablename__ = "accepted_id_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    # A supported document can remain visible to the Administrator but be
    # unavailable for new registrations until local policy accepts it.
    is_accepted_for_registration = db.Column(
        db.Boolean, nullable=False, default=False, index=True
    )


class ResidentProfile(db.Model, TimestampMixin):
    __tablename__ = "resident_profiles"
    __table_args__ = (
        Index("ix_resident_profiles_approval_created", "approval_status", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    date_of_birth = db.Column(db.Date)
    sex = db.Column(db.String(40))
    civil_status = db.Column(db.String(40))
    house_number_street = db.Column(db.String(180))
    purok_zone = db.Column(db.String(120))
    resident_type_id = db.Column(
        db.Integer, db.ForeignKey("resident_types.id"), index=True
    )
    resident_type_other = db.Column(db.String(150))
    residency_status = db.Column(db.String(40), index=True)
    years_of_residency = db.Column(db.Integer)
    occupation = db.Column(db.String(150))
    is_household_head = db.Column(db.Boolean, nullable=False, default=False)
    household_number = db.Column(db.String(80))
    emergency_contact_name = db.Column(db.String(150))
    emergency_contact_relationship = db.Column(db.String(80))
    emergency_contact_number = db.Column(db.String(20))
    accepted_id_type_id = db.Column(
        db.Integer, db.ForeignKey("accepted_id_types.id"), index=True
    )
    valid_id_number = db.Column(db.String(120))
    valid_id_type_other = db.Column(db.String(150))
    valid_id_file_path = db.Column(db.String(255))
    id_verification_status = db.Column(
        db.String(30), nullable=False, default="Pending Verification", index=True
    )
    id_verified_by = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    id_verified_at = db.Column(db.DateTime)
    id_rejection_reason = db.Column(db.Text)
    address_verification_status = db.Column(
        db.String(30), nullable=False, default="Pending Verification", index=True
    )
    address_verified_by = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    address_verified_at = db.Column(db.DateTime)
    address_rejection_reason = db.Column(db.Text)
    privacy_consent = db.Column(db.Boolean, nullable=False, default=False)
    privacy_consented_at = db.Column(db.DateTime)
    approval_status = db.Column(
        db.String(20), nullable=False, default="Pending", index=True
    )
    approval_remarks = db.Column(db.Text)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    reviewed_at = db.Column(db.DateTime)
    approval_notice_seen_at = db.Column(db.DateTime)

    user = db.relationship(
        "User", back_populates="resident_profile", foreign_keys=[user_id]
    )
    resident_type = db.relationship("ResidentType")
    accepted_id_type = db.relationship("AcceptedIdType")
    reviewer = db.relationship("User", foreign_keys=[reviewed_by])
    id_verifier = db.relationship("User", foreign_keys=[id_verified_by])
    address_verifier = db.relationship("User", foreign_keys=[address_verified_by])


class PermitType(db.Model, TimestampMixin):
    __tablename__ = "permit_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.Text)
    requirements = db.Column(db.Text)
    fee = db.Column(db.Numeric(10, 2))
    fee_is_configured = db.Column(db.Boolean, nullable=False, default=False)
    validity_days = db.Column(db.Integer, default=365)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class EventCategory(db.Model, TimestampMixin):
    __tablename__ = "event_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.Text)
    fee = db.Column(db.Numeric(10, 2))
    fee_is_configured = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class EventVenue(db.Model, TimestampMixin):
    __tablename__ = "event_venues"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), unique=True, nullable=False)
    address = db.Column(db.String(255))
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)


class PermitApplication(db.Model, TimestampMixin):
    __tablename__ = "permit_applications"

    id = db.Column(db.Integer, primary_key=True)
    reference_no = db.Column(
        db.String(40),
        unique=True,
        nullable=False,
        default=lambda: make_reference("PRM"),
    )
    applicant_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    permit_type_id = db.Column(
        db.Integer, db.ForeignKey("permit_types.id"), nullable=False
    )
    purpose = db.Column(db.Text, nullable=False)
    fee_at_submission = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    status = db.Column(
        db.String(30), nullable=False, default="Pending", index=True
    )
    application_date = db.Column(db.DateTime, default=utcnow)
    appointment_date = db.Column(db.DateTime)
    decision_date = db.Column(db.DateTime)
    decided_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    remarks = db.Column(db.Text)
    permit_file_path = db.Column(db.String(255))
    attachment_path = db.Column(db.String(255))

    applicant = db.relationship(
        "User", foreign_keys=[applicant_id], backref="permit_applications"
    )
    permit_type = db.relationship("PermitType")


class EventRequest(db.Model, TimestampMixin):
    __tablename__ = "event_requests"

    id = db.Column(db.Integer, primary_key=True)
    reference_no = db.Column(
        db.String(40),
        unique=True,
        nullable=False,
        default=lambda: make_reference("EVT"),
    )
    requester_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    event_name = db.Column(db.String(180), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("event_categories.id"))
    venue_id = db.Column(db.Integer, db.ForeignKey("event_venues.id"), index=True)
    # These two labels remain as immutable legacy/snapshot values.
    event_type = db.Column(db.String(120), nullable=False, default="")
    description = db.Column(db.Text, nullable=False)
    proposed_date = db.Column(db.Date, nullable=False)
    proposed_start_time = db.Column(db.Time)
    proposed_end_time = db.Column(db.Time)
    proposed_location = db.Column(db.String(255), nullable=False, default="")
    # Deprecated storage-only fields; the revised form does not expose them.
    expected_attendees = db.Column(db.Integer, nullable=False, default=0)
    supporting_document_path = db.Column(db.String(255))
    fee_at_submission = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    status = db.Column(
        db.String(30), nullable=False, default="Pending", index=True
    )
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    decision_date = db.Column(db.DateTime)
    remarks = db.Column(db.Text)

    requester = db.relationship(
        "User", foreign_keys=[requester_id], backref="event_requests"
    )
    category = db.relationship("EventCategory")
    venue = db.relationship("EventVenue", backref="event_requests")
    reservation = db.relationship(
        "EventReservation",
        back_populates="event_request",
        uselist=False,
        cascade="all, delete-orphan",
    )


class EventReservation(db.Model, TimestampMixin):
    """A temporary venue hold or a reservation bound to an event request."""

    __tablename__ = "event_reservations"
    __table_args__ = (
        Index(
            "ix_event_reservation_venue_interval",
            "venue_id",
            "starts_at",
            "ends_at",
        ),
        Index(
            "ix_event_reservation_status_expiry", "status", "hold_expires_at"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    venue_id = db.Column(
        db.Integer, db.ForeignKey("event_venues.id"), nullable=False, index=True
    )
    event_request_id = db.Column(
        db.Integer,
        db.ForeignKey("event_requests.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    requester_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    starts_at = db.Column(db.DateTime, nullable=False)
    ends_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Held", index=True)
    hold_token_hash = db.Column(db.String(64), unique=True, index=True)
    hold_expires_at = db.Column(db.DateTime)
    draft_payload = db.Column(db.Text)
    released_at = db.Column(db.DateTime)
    release_reason = db.Column(db.String(255))

    venue = db.relationship("EventVenue", backref="reservations")
    event_request = db.relationship(
        "EventRequest", back_populates="reservation", foreign_keys=[event_request_id]
    )
    requester = db.relationship("User", foreign_keys=[requester_id])


class BlotterCase(db.Model, TimestampMixin):
    __tablename__ = "blotter_cases"

    id = db.Column(db.Integer, primary_key=True)
    case_no = db.Column(
        db.String(40), unique=True, nullable=False, default=lambda: make_reference("BLT")
    )
    complainant_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    respondent_name = db.Column(db.String(150), nullable=False)
    respondent_address = db.Column(db.String(255))
    incident_type = db.Column(db.String(120), nullable=False)
    incident_date = db.Column(db.DateTime, nullable=False)
    incident_location = db.Column(db.String(255), nullable=False)
    narrative = db.Column(db.Text, nullable=False)
    supporting_document_path = db.Column(db.String(255))
    status = db.Column(db.String(30), nullable=False, default="Filed", index=True)
    filed_date = db.Column(db.DateTime, default=utcnow)
    assigned_officer = db.Column(db.String(150))
    resolution = db.Column(db.Text)
    hearing_date = db.Column(db.DateTime)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    complainant = db.relationship(
        "User", foreign_keys=[complainant_id], backref="blotter_cases"
    )


class Schedule(db.Model, TimestampMixin):
    __tablename__ = "schedules"

    id = db.Column(db.Integer, primary_key=True)
    reference_no = db.Column(
        db.String(40), unique=True, nullable=False, default=lambda: make_reference("SCH")
    )
    related_type = db.Column(db.String(20), nullable=False, index=True)
    related_id = db.Column(db.Integer, nullable=False, index=True)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    scheduled_datetime = db.Column(db.DateTime, nullable=False, index=True)
    purpose = db.Column(db.String(255), nullable=False)
    note = db.Column(db.Text)
    status = db.Column(db.String(30), nullable=False, default="Requested", index=True)
    confirmed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    requester = db.relationship("User", foreign_keys=[requested_by])


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    recipient_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recipient_contact = db.Column(db.String(150))
    title = db.Column(db.String(180), nullable=False, default="System notification")
    message = db.Column(db.Text, nullable=False)
    channel = db.Column(db.String(20), nullable=False, default="in_app", index=True)
    status = db.Column(db.String(20), nullable=False, default="sent")
    related_type = db.Column(db.String(30))
    related_id = db.Column(db.Integer)
    sent_at = db.Column(db.DateTime)
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    user = db.relationship("User", foreign_keys=[user_id])


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    actor_role = db.Column(db.String(20))
    action = db.Column(db.String(120), nullable=False, index=True)
    resource = db.Column(db.String(50), nullable=False, default="system", index=True)
    target_reference = db.Column(db.String(80))
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    user_agent = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user = db.relationship("User")


class Report(db.Model):
    __tablename__ = "reports"

    id = db.Column(db.Integer, primary_key=True)
    generated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    report_type = db.Column(db.String(50), nullable=False)
    format = db.Column(db.String(10), nullable=False)
    filters_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    user = db.relationship("User")

    @classmethod
    def issue(cls, user, lifetime_minutes=30):
        cls.query.filter_by(user_id=user.id, used_at=None).update({"used_at": utcnow()})
        raw = secrets.token_urlsafe(32)
        record = cls(
            user_id=user.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(minutes=lifetime_minutes),
        )
        db.session.add(record)
        return raw, record

    @classmethod
    def valid_for(cls, raw):
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return cls.query.filter(
            cls.token_hash == digest,
            cls.used_at.is_(None),
            cls.expires_at > utcnow(),
        ).first()


class SystemSetting(db.Model, TimestampMixin):
    __tablename__ = "system_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)


class Announcement(db.Model, TimestampMixin):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    body = db.Column(db.Text, nullable=False)
    is_published = db.Column(db.Boolean, nullable=False, default=True, index=True)
    published_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    expires_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    author = db.relationship("User", foreign_keys=[created_by])


def log_activity(
    user_id,
    action,
    details=None,
    ip_address=None,
    resource="system",
    target_reference=None,
    actor_role=None,
    user_agent=None,
    commit=True,
):
    if actor_role is None and user_id:
        user = db.session.get(User, user_id)
        actor_role = user.role if user else None
    entry = ActivityLog(
        user_id=user_id,
        actor_role=actor_role,
        action=action,
        resource=resource,
        target_reference=target_reference,
        details=details,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:255],
    )
    db.session.add(entry)
    if commit:
        db.session.commit()
    return entry
