"""Shared payment, publication and requirement rules; timestamps stored as UTC."""
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import or_
from models import Announcement, PaymentAuthority, db, utcnow

LOCAL_TIMEZONE = timezone(timedelta(hours=8), "Asia/Manila")
CATEGORIES = ("General Advisory", "Emergency", "Office Closure", "Event", "Scholarship",
              "Ayuda / Assistance", "Health", "Clean-up / Community Activity", "Service Interruption", "Other")


def local_datetime(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)
        return parsed.astimezone(UTC).replace(tzinfo=None)
    except (ValueError, TypeError):
        raise ValueError("Enter a valid date and time (Asia/Manila).") from None


def local_display(value):
    return value.replace(tzinfo=UTC).astimezone(LOCAL_TIMEZONE).strftime("%b %d, %Y %I:%M %p") if value else "—"


def payment_status(item):
    if Decimal(item.fee_at_submission or 0) <= 0:
        return "Not Required"
    return item.payment.status if item.payment else "Unpaid"


def payment_eligible(item):
    return payment_status(item) in {"Not Required", "Paid", "Waived"}


def can_record_payment(user):
    return user.role == "admin" or (user.role == "staff" and db.session.get(PaymentAuthority, user.id) is not None)


def active_announcements():
    current = utcnow()
    return Announcement.query.filter(
        Announcement.is_published.is_(True),
        Announcement.status.in_(("Scheduled", "Published")),
        or_(Announcement.publish_start_at.is_(None), Announcement.publish_start_at <= current),
        or_(Announcement.expires_at.is_(None), Announcement.expires_at > current),
    )


def refresh_announcements(audit):
    current = utcnow()
    # Only visit records at a lifecycle boundary, not the whole announcement history.
    rows = Announcement.query.filter(Announcement.is_published.is_(True),
        Announcement.status.in_(("Published", "Scheduled")),
        or_(Announcement.expires_at <= current,
            db.and_(Announcement.status == "Scheduled", Announcement.publish_start_at <= current))).all()
    for item in rows:
        target = "Expired" if item.expires_at and current >= item.expires_at else "Published"
        old = item.status
        changed = Announcement.query.filter_by(id=item.id, status=old).update({"status": target}, synchronize_session=False)
        if changed:
            item.status = target
            if target == "Expired":
                item.expired_at = item.expires_at
            else:
                item.published_at = item.publish_start_at or current
            audit("ANNOUNCEMENT_" + target.upper(), "announcement", str(item.id))
    if rows:
        db.session.commit()


def requirement_submission(definition, form, files, save_upload, resident):
    """Shared typed validation for permit and program requirement snapshots."""
    key = f"requirement_{definition.id}"
    value = form.get(key + "_value", "").strip() or None
    uploaded = files.get(key + "_file")
    has_upload = bool(uploaded and uploaded.filename)
    kind = definition.requirement_type
    if kind == "System Check":
        profile = resident.resident_profile
        if not profile or profile.approval_status != "Approved":
            if definition.is_required:
                raise ValueError(f"{definition.name} could not be confirmed by the system.")
            return None, None, "Not Submitted"
        return "System check passed", None, "Verified"
    present = has_upload if kind == "Document" else bool(value)
    if definition.is_required and not present:
        raise ValueError(f"{definition.name} is required before submission.")
    path = save_upload(key + "_file") if has_upload and kind == "Document" else None
    return value if kind != "Document" else None, path, "Submitted" if present else "Not Submitted"
