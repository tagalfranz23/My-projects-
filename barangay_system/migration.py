"""Non-destructive, idempotent SQLite schema upgrades.

The migration only creates missing tables, columns, and ORM-declared indexes;
it never drops/recreates users or operational records.
"""

from datetime import datetime

from sqlalchemy import inspect, text

from models import ResidentProfile, User, db


ADDITIONS = {
    "users": {"updated_at": "DATETIME"},
    "resident_types": {
        "description": "TEXT",
        "is_active": "BOOLEAN NOT NULL DEFAULT 1",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    },
    "accepted_id_types": {
        "description": "TEXT",
        "is_active": "BOOLEAN NOT NULL DEFAULT 1",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
        "is_accepted_for_registration": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "resident_profiles": {
        "date_of_birth": "DATE",
        "sex": "VARCHAR(40)",
        "civil_status": "VARCHAR(40)",
        "house_number_street": "VARCHAR(180)",
        "purok_zone": "VARCHAR(120)",
        "resident_type_id": "INTEGER",
        "resident_type_other": "VARCHAR(150)",
        "residency_status": "VARCHAR(40)",
        "years_of_residency": "INTEGER",
        "occupation": "VARCHAR(150)",
        "is_household_head": "BOOLEAN NOT NULL DEFAULT 0",
        "household_number": "VARCHAR(80)",
        "emergency_contact_name": "VARCHAR(150)",
        "emergency_contact_relationship": "VARCHAR(80)",
        "emergency_contact_number": "VARCHAR(20)",
        "accepted_id_type_id": "INTEGER",
        "valid_id_number": "VARCHAR(120)",
        "valid_id_type_other": "VARCHAR(150)",
        "valid_id_file_path": "VARCHAR(255)",
        "id_verification_status": "VARCHAR(30) NOT NULL DEFAULT 'Pending Verification'",
        "id_verified_by": "INTEGER",
        "id_verified_at": "DATETIME",
        "id_rejection_reason": "TEXT",
        "address_verification_status": "VARCHAR(30) NOT NULL DEFAULT 'Pending Verification'",
        "address_verified_by": "INTEGER",
        "address_verified_at": "DATETIME",
        "address_rejection_reason": "TEXT",
        "privacy_consent": "BOOLEAN NOT NULL DEFAULT 0",
        "privacy_consented_at": "DATETIME",
        "approval_status": "VARCHAR(20) NOT NULL DEFAULT 'Pending'",
        "approval_remarks": "TEXT",
        "reviewed_by": "INTEGER",
        "reviewed_at": "DATETIME",
        "approval_notice_seen_at": "DATETIME",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    },
    "permit_types": {
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
        "fee_is_configured": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "event_categories": {
        "description": "TEXT",
        "fee": "DECIMAL(10,2)",
        "fee_is_configured": "BOOLEAN NOT NULL DEFAULT 0",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    },
    "event_venues": {
        "address": "VARCHAR(255)",
        "description": "TEXT",
        "is_active": "BOOLEAN NOT NULL DEFAULT 1",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    },
    "permit_applications": {
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
        "reviewed_by": "INTEGER",
        "attachment_path": "VARCHAR(255)",
        "fee_at_submission": "DECIMAL(10,2) NOT NULL DEFAULT 0",
    },
    "event_requests": {
        "category_id": "INTEGER",
        "venue_id": "INTEGER",
        "proposed_start_time": "TIME",
        "proposed_end_time": "TIME",
        "supporting_document_path": "VARCHAR(255)",
        "fee_at_submission": "DECIMAL(10,2) NOT NULL DEFAULT 0",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    },
    "blotter_cases": {
        # Nullable when added to a legacy table: the migration must not invent a
        # respondent for historical complaints. New submissions are validated.
        "respondent_name": "VARCHAR(150)",
        "respondent_address": "VARCHAR(255)",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
        "supporting_document_path": "VARCHAR(255)",
    },
    "notifications": {
        "user_id": "INTEGER",
        "title": "VARCHAR(180) DEFAULT 'System notification'",
        "channel": "VARCHAR(20) DEFAULT 'in_app'",
        "read_at": "DATETIME",
        "created_at": "DATETIME",
    },
    "activity_logs": {
        "actor_role": "VARCHAR(20)",
        "resource": "VARCHAR(50) DEFAULT 'system'",
        "target_reference": "VARCHAR(80)",
        "user_agent": "VARCHAR(255)",
    },
    "schedules": {
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    },
    "reports": {"filters_json": "TEXT"},
    "announcements": {"updated_at": "DATETIME"},
}


def _add_missing_columns():
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    quote = db.engine.dialect.identifier_preparer.quote
    added_columns = set()
    for table, columns in ADDITIONS.items():
        if table not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name in existing:
                continue
            db.session.execute(
                text(
                    f"ALTER TABLE {quote(table)} ADD COLUMN "
                    f"{quote(name)} {ddl}"
                )
            )
            added_columns.add((table, name))
    return added_columns


def _create_missing_indexes():
    """Create indexes declared by the current ORM after all columns exist."""
    for table in db.metadata.sorted_tables:
        for index in table.indexes:
            index.create(bind=db.engine, checkfirst=True)


def _normalize_legacy_rows(added_columns=None):
    """Normalize pre-existing values without undoing later Admin decisions."""
    added_columns = added_columns or set()
    tables = set(inspect(db.engine).get_table_names())
    if "blotter_cases" in tables:
        db.session.execute(
            text("UPDATE blotter_cases SET status='Filed' WHERE status='Pending'")
        )
        db.session.execute(
            text(
                "UPDATE blotter_cases SET status='For Investigation' "
                "WHERE status='Ongoing'"
            )
        )
        db.session.execute(
            text("UPDATE blotter_cases SET status='Closed' WHERE status='Dismissed'")
        )
    if "permit_applications" in tables:
        db.session.execute(
            text(
                "UPDATE permit_applications SET status='Completed' "
                "WHERE status='Released'"
            )
        )
        db.session.execute(
            text(
                "UPDATE permit_applications SET fee_at_submission="
                "COALESCE((SELECT fee FROM permit_types "
                "WHERE permit_types.id=permit_applications.permit_type_id), 0) "
                "WHERE fee_at_submission IS NULL"
            )
        )
    if "event_requests" in tables:
        db.session.execute(
            text(
                "UPDATE event_requests SET fee_at_submission="
                "COALESCE((SELECT fee FROM event_categories "
                "WHERE event_categories.id=event_requests.category_id), 0) "
                "WHERE fee_at_submission IS NULL"
            )
        )
    if "notifications" in tables:
        db.session.execute(
            text(
                "UPDATE notifications SET user_id=recipient_user_id "
                "WHERE user_id IS NULL"
            )
        )
        db.session.execute(
            text("UPDATE notifications SET channel='in_app' WHERE channel IS NULL")
        )
    if (
        "accepted_id_types" in tables
        and ("accepted_id_types", "is_accepted_for_registration") in added_columns
    ):
        # Before this flag existed, active entries were the Administrator's
        # registration-accepted list. Preserve that decision during upgrade.
        db.session.execute(
            text(
                "UPDATE accepted_id_types SET is_accepted_for_registration=1 "
                "WHERE is_active=1 AND is_accepted_for_registration=0"
            )
        )
    if "resident_profiles" in tables:
        # Existing approved accounts are preserved as verified by this explicit
        # legacy migration decision. Pending accounts enter the new review flow.
        db.session.execute(
            text(
                "UPDATE resident_profiles SET residency_status='Permanent Resident' "
                "WHERE residency_status IS NULL AND approval_status='Approved'"
            )
        )
        verification_columns_added = {
            ("resident_profiles", "id_verification_status"),
            ("resident_profiles", "address_verification_status"),
        }
        if verification_columns_added & added_columns:
            db.session.execute(
                text(
                    "UPDATE resident_profiles SET id_verification_status='Verified', "
                    "address_verification_status='Verified' "
                    "WHERE approval_status='Approved'"
                )
            )


def _ensure_required_reference_catalogs():
    """Add only policy-neutral resident types and supported ID choices.

    Supported IDs start as *not accepted* for registration. The Administrator
    makes that local policy decision in System Configuration. Existing entries
    are never overwritten or deleted.
    """
    from models import AcceptedIdType, ResidentType

    resident_types = (
        "Homeowner",
        "Renter / Tenant",
        "Household Member",
        "Boarder / Bedspacer",
        "Caretaker",
        "Temporary Resident",
        "Others",
    )
    supported_ids = (
        "Philippine National ID / PhilSys ID",
        "Digital National ID",
        "Passport",
        "Driver's License",
        "UMID",
        "SSS ID",
        "GSIS ID",
        "PRC ID",
        "Voter's ID / Voter's Certification",
        "Postal ID",
        "PhilHealth ID",
        "Pag-IBIG Loyalty Card / ID",
        "Senior Citizen ID",
        "PWD ID",
        "School ID / Student ID",
        "Company / Employee ID",
        "Barangay ID",
        "NBI Clearance",
        "Police Clearance",
        "Others",
    )
    for name in resident_types:
        if not ResidentType.query.filter_by(name=name).first():
            db.session.add(ResidentType(name=name, is_active=True))
    for name in supported_ids:
        if not AcceptedIdType.query.filter_by(name=name).first():
            db.session.add(
                AcceptedIdType(
                    name=name,
                    is_active=True,
                    is_accepted_for_registration=False,
                )
            )


def _create_legacy_profiles():
    """Give pre-revision Resident/Staff accounts approved legacy profiles."""
    now = datetime.utcnow()
    users = User.query.filter(User.role.in_(("resident", "staff"))).all()
    for user in users:
        if user.resident_profile:
            continue
        db.session.add(
            ResidentProfile(
                user_id=user.id,
                house_number_street=user.address,
                privacy_consent=False,
                approval_status="Approved",
                residency_status="Permanent Resident",
                id_verification_status="Verified",
                address_verification_status="Verified",
                reviewed_at=now,
                approval_remarks="Legacy account preserved during additive migration.",
            )
        )


def upgrade_legacy_schema():
    """Upgrade the configured database without replacing existing records."""
    try:
        # New tables are created first. create_all never drops existing data.
        db.create_all()
        added_columns = _add_missing_columns()
        _create_missing_indexes()
        db.session.commit()
        # Refresh ORM/inspector visibility after DDL before data normalization.
        _normalize_legacy_rows(added_columns)
        _create_legacy_profiles()
        _ensure_required_reference_catalogs()
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
