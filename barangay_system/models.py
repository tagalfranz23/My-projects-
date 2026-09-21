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
    processing_time = db.Column(db.String(180))
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
    request_source = db.Column(db.String(30), nullable=False, default="Online", index=True)
    encoded_by_staff_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    office_name = db.Column(db.String(150))
    endorsed_by = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    endorsed_at = db.Column(db.DateTime)
    endorsement_note = db.Column(db.Text)
    signed_by = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    signed_at = db.Column(db.DateTime)
    signatory_name = db.Column(db.String(150))
    signatory_title = db.Column(db.String(150))
    signature_path = db.Column(db.String(255))
    document_version = db.Column(db.Integer, nullable=False, default=0)
    payment_confirmed_at = db.Column(db.DateTime)
    payment_confirmed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    payment_reference = db.Column(db.String(120))
    released_by = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    released_at = db.Column(db.DateTime)

    applicant = db.relationship(
        "User", foreign_keys=[applicant_id], backref="permit_applications"
    )
    permit_type = db.relationship("PermitType")
    requirement_records = db.relationship(
        "ApplicationRequirement", back_populates="application", order_by="ApplicationRequirement.id"
    )

    @property
    def required_requirements_verified(self):
        return all(
            record.status == "Verified" for record in self.requirement_records if record.is_required
        )

    @property
    def is_signed(self):
        return bool(self.signed_at and self.signed_by and self.signature_path)


class ServiceRequirement(db.Model, TimestampMixin):
    """Administrator-owned requirement definitions for a permit service."""

    __tablename__ = "service_requirements"
    id = db.Column(db.Integer, primary_key=True)
    permit_type_id = db.Column(
        db.Integer, db.ForeignKey("permit_types.id"), nullable=False, index=True
    )
    name = db.Column(db.String(180), nullable=False)
    requirement_type = db.Column(db.String(30), nullable=False, default="Document")
    instructions = db.Column(db.Text)
    is_required = db.Column(db.Boolean, nullable=False, default=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    permit_type = db.relationship("PermitType", backref="service_requirements")
    __table_args__ = (db.UniqueConstraint("permit_type_id", "name"),)


class ApplicationRequirement(db.Model):
    """Immutable requirement snapshot and current review state for one request."""

    __tablename__ = "application_requirements"
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(
        db.Integer, db.ForeignKey("permit_applications.id"), nullable=False, index=True
    )
    definition_id = db.Column(db.Integer, db.ForeignKey("service_requirements.id"), nullable=False)
    name = db.Column(db.String(180), nullable=False)
    requirement_type = db.Column(db.String(30), nullable=False)
    instructions = db.Column(db.Text)
    is_required = db.Column(db.Boolean, nullable=False)
    status = db.Column(db.String(30), nullable=False, default="Not Submitted", index=True)
    value = db.Column(db.Text)
    file_path = db.Column(db.String(255))
    submitted_at = db.Column(db.DateTime)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    reviewed_at = db.Column(db.DateTime)
    review_note = db.Column(db.Text)
    application = db.relationship("PermitApplication", back_populates="requirement_records")
    reviewer = db.relationship("User", foreign_keys=[reviewed_by])
    definition = db.relationship("ServiceRequirement")
    history = db.relationship("RequirementHistory", back_populates="requirement", order_by="RequirementHistory.id")
    __table_args__ = (db.UniqueConstraint("application_id", "definition_id"),)


class RequirementHistory(db.Model):
    """Append-only upload and review events, preserving replacement evidence."""

    __tablename__ = "requirement_history"
    id = db.Column(db.Integer, primary_key=True)
    requirement_id = db.Column(
        db.Integer, db.ForeignKey("application_requirements.id"), nullable=False, index=True
    )
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    actor_role = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    note = db.Column(db.Text)
    file_path = db.Column(db.String(255))
    value = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    requirement = db.relationship("ApplicationRequirement", back_populates="history")
    actor = db.relationship("User", foreign_keys=[actor_id])


class PermitSignatory(db.Model, TimestampMixin):
    """Restricted electronic-signature configuration for an authorized admin."""

    __tablename__ = "permit_signatories"
    id = db.Column(db.Integer, primary_key=True)
    permit_type_id = db.Column(
        db.Integer, db.ForeignKey("permit_types.id"), nullable=False, unique=True
    )
    authorized_admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    permit_type = db.relationship("PermitType")
    authorized_admin = db.relationship("User", foreign_keys=[authorized_admin_id])


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
    status = db.Column(db.String(20), nullable=False, default="Draft", index=True)
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    published_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    expires_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    author = db.relationship("User", foreign_keys=[created_by])


class QueueEntry(db.Model, TimestampMixin):
    __tablename__ = "queue_entries"
    __table_args__ = (db.UniqueConstraint("queue_date", "queue_number"),)

    id = db.Column(db.Integer, primary_key=True)
    queue_date = db.Column(db.Date, nullable=False, index=True)
    queue_number = db.Column(db.String(30), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    resident_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    permit_application_id = db.Column(db.Integer, db.ForeignKey("permit_applications.id"), index=True)
    status = db.Column(db.String(20), nullable=False, default="Waiting", index=True)
    priority_category = db.Column(db.String(80))
    called_at = db.Column(db.DateTime)
    served_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    resident = db.relationship("User", foreign_keys=[resident_id])
    creator = db.relationship("User", foreign_keys=[created_by])
    permit_application = db.relationship("PermitApplication")


class RequestAssignment(db.Model, TimestampMixin):
    __tablename__ = "request_assignments"
    id = db.Column(db.Integer, primary_key=True)
    permit_application_id = db.Column(db.Integer, db.ForeignKey("permit_applications.id"), nullable=False, index=True)
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Active", index=True)
    note = db.Column(db.Text)
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_user_id])
    assigner = db.relationship("User", foreign_keys=[assigned_by])
    permit_application = db.relationship("PermitApplication")


class GeneralConcern(db.Model, TimestampMixin):
    __tablename__ = "general_concerns"
    id = db.Column(db.Integer, primary_key=True)
    reference_no = db.Column(db.String(40), unique=True, nullable=False, default=lambda: make_reference("CON"))
    resident_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    category = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    location = db.Column(db.String(255))
    sensitivity_level = db.Column(db.String(20), nullable=False, default="Standard", index=True)
    assigned_to = db.Column(db.Integer, db.ForeignKey("users.id"))
    status = db.Column(db.String(30), nullable=False, default="Filed", index=True)
    encoded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    resident = db.relationship("User", foreign_keys=[resident_id])
    encoder = db.relationship("User", foreign_keys=[encoded_by])


class ResidentUpdateRequest(db.Model, TimestampMixin):
    __tablename__ = "resident_update_requests"
    id = db.Column(db.Integer, primary_key=True)
    resident_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    field_name = db.Column(db.String(80), nullable=False)
    old_value = db.Column(db.Text)
    proposed_value = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Pending", index=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    resident = db.relationship("User", foreign_keys=[resident_id])
    reviewer = db.relationship("User", foreign_keys=[reviewed_by])


class Household(db.Model, TimestampMixin):
    __tablename__ = "households"
    id = db.Column(db.Integer, primary_key=True)
    household_number = db.Column(db.String(80), unique=True, nullable=False, index=True)
    address = db.Column(db.String(255))
    head_resident_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    status = db.Column(db.String(20), nullable=False, default="Active", index=True)
    head_resident = db.relationship("User", foreign_keys=[head_resident_id])
    members = db.relationship("HouseholdMember", back_populates="household")


class HouseholdMember(db.Model, TimestampMixin):
    __tablename__ = "household_members"
    __table_args__ = (db.UniqueConstraint("household_id", "resident_id"),)
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    resident_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    membership_status = db.Column(db.String(20), nullable=False, default="Active", index=True)
    joined_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    left_at = db.Column(db.DateTime)
    household = db.relationship("Household", back_populates="members")
    resident = db.relationship("User", foreign_keys=[resident_id])


class AssistanceReferral(db.Model, TimestampMixin):
    __tablename__ = "assistance_referrals"
    id = db.Column(db.Integer, primary_key=True)
    reference_no = db.Column(db.String(40), unique=True, nullable=False, default=lambda: make_reference("ASR"))
    resident_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    category = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey("users.id"))
    status = db.Column(db.String(30), nullable=False, default="Open", index=True)
    sensitivity_level = db.Column(db.String(20), nullable=False, default="Restricted")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    resident = db.relationship("User", foreign_keys=[resident_id])
    creator = db.relationship("User", foreign_keys=[created_by])


class ResidentFeedback(db.Model, TimestampMixin):
    __tablename__ = "resident_feedback"
    __table_args__ = (db.UniqueConstraint("resident_id", "permit_application_id"),)
    id = db.Column(db.Integer, primary_key=True)
    resident_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    permit_application_id = db.Column(db.Integer, db.ForeignKey("permit_applications.id"), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    resident = db.relationship("User", foreign_keys=[resident_id])
    permit_application = db.relationship("PermitApplication")


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
