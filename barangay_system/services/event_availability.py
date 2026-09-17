"""Centralized, transaction-safe event venue availability rules."""

import hashlib
import json
import secrets
from datetime import datetime, timedelta

from sqlalchemy import and_, or_

from models import EventRequest, EventReservation, EventVenue, db


BLOCKING_EVENT_STATUSES = frozenset(
    {"Pending", "Under Review", "Endorsed to Admin", "Approved"}
)
DEFAULT_HOLD_MINUTES = 10
MAX_RECOMMENDATION_DAYS = 365


class ReservationConflict(RuntimeError):
    """Raised when an interval is no longer available at commit time."""


class InvalidHold(RuntimeError):
    """Raised for missing, expired, released, or incorrectly owned holds."""


def _digest(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _lock_venue(venue_id):
    query = db.session.query(EventVenue).filter(
        EventVenue.id == venue_id, EventVenue.is_active.is_(True)
    )
    venue = query.first()
    if not venue:
        raise ValueError("The selected venue is not available for requests.")
    return venue


def expire_stale_holds(moment=None):
    moment = moment or datetime.utcnow()
    stale = EventReservation.query.filter(
        EventReservation.status == "Held",
        EventReservation.hold_expires_at.isnot(None),
        EventReservation.hold_expires_at <= moment,
    ).all()
    for reservation in stale:
        reservation.status = "Expired"
        reservation.released_at = moment
        reservation.release_reason = "Temporary recommendation hold expired."
    return len(stale)


def find_conflict(venue_id, starts_at, ends_at, exclude_reservation_id=None, lock=False):
    if not starts_at or not ends_at or ends_at <= starts_at:
        raise ValueError("Event end time must be later than its start time.")
    moment = datetime.utcnow()
    query = (
        db.session.query(EventReservation)
        .outerjoin(EventRequest, EventReservation.event_request_id == EventRequest.id)
        .filter(
            EventReservation.venue_id == venue_id,
            EventReservation.starts_at < ends_at,
            EventReservation.ends_at > starts_at,
            or_(
                and_(
                    EventReservation.status == "Held",
                    EventReservation.hold_expires_at > moment,
                ),
                and_(
                    EventReservation.status == "Reserved",
                    EventRequest.status.in_(BLOCKING_EVENT_STATUSES),
                ),
            ),
        )
        .order_by(EventReservation.created_at, EventReservation.id)
    )
    if exclude_reservation_id:
        query = query.filter(EventReservation.id != exclude_reservation_id)
    return query.first()


def is_available(venue_id, starts_at, ends_at, exclude_reservation_id=None):
    return find_conflict(
        venue_id, starts_at, ends_at, exclude_reservation_id
    ) is None


def find_next_available(venue_id, starts_at, ends_at, max_days=MAX_RECOMMENDATION_DAYS):
    duration = ends_at - starts_at
    for day_offset in range(1, max_days + 1):
        candidate_start = starts_at + timedelta(days=day_offset)
        candidate_end = candidate_start + duration
        if is_available(venue_id, candidate_start, candidate_end):
            return candidate_start, candidate_end
    return None


def reserve_event(event_request, venue_id, starts_at, ends_at):
    """Lock the venue, re-check overlap, and stage the first valid reservation."""
    _lock_venue(venue_id)
    expire_stale_holds()
    if find_conflict(venue_id, starts_at, ends_at, lock=True):
        raise ReservationConflict("The selected venue and schedule are unavailable.")
    reservation = EventReservation(
        venue_id=venue_id,
        event_request=event_request,
        requester_id=event_request.requester_id,
        starts_at=starts_at,
        ends_at=ends_at,
        status="Reserved",
    )
    db.session.add(reservation)
    db.session.flush()
    return reservation


def create_hold(
    venue_id,
    requester_id,
    starts_at,
    ends_at,
    draft_payload,
    hold_minutes=DEFAULT_HOLD_MINUTES,
):
    """Create a private temporary hold and return ``(raw_token, reservation)``."""
    _lock_venue(venue_id)
    expire_stale_holds()
    if find_conflict(venue_id, starts_at, ends_at, lock=True):
        raise ReservationConflict("The recommended schedule is no longer available.")
    raw_token = secrets.token_urlsafe(32)
    reservation = EventReservation(
        venue_id=venue_id,
        requester_id=requester_id,
        starts_at=starts_at,
        ends_at=ends_at,
        status="Held",
        hold_token_hash=_digest(raw_token),
        hold_expires_at=datetime.utcnow() + timedelta(minutes=hold_minutes),
        draft_payload=json.dumps(draft_payload, separators=(",", ":")),
    )
    db.session.add(reservation)
    db.session.flush()
    return raw_token, reservation


def get_hold(raw_token, requester_id=None, lock=False):
    query = EventReservation.query.filter_by(hold_token_hash=_digest(raw_token))
    hold = query.first()
    if not hold or (requester_id is not None and hold.requester_id != requester_id):
        raise InvalidHold("The recommended schedule hold was not found.")
    if hold.status != "Held" or not hold.hold_expires_at:
        raise InvalidHold("The recommended schedule hold is no longer active.")
    if hold.hold_expires_at <= datetime.utcnow():
        hold.status = "Expired"
        hold.released_at = datetime.utcnow()
        hold.release_reason = "Temporary recommendation hold expired."
        raise InvalidHold("The recommended schedule hold has expired.")
    return hold


def accept_hold(raw_token, requester_id, event_request):
    hold = get_hold(raw_token, requester_id, lock=True)
    _lock_venue(hold.venue_id)
    if find_conflict(
        hold.venue_id,
        hold.starts_at,
        hold.ends_at,
        exclude_reservation_id=hold.id,
        lock=True,
    ):
        raise ReservationConflict("The recommended schedule is no longer available.")
    hold.event_request = event_request
    hold.status = "Reserved"
    hold.hold_expires_at = None
    hold.hold_token_hash = None
    hold.draft_payload = None
    db.session.flush()
    return hold


def decline_hold(raw_token, requester_id, reason="Resident chose another schedule."):
    hold = get_hold(raw_token, requester_id, lock=True)
    hold.status = "Released"
    hold.released_at = datetime.utcnow()
    hold.release_reason = reason
    hold.hold_token_hash = None
    hold.draft_payload = None
    db.session.flush()
    return hold


def release_event_reservation(event_request, reason):
    reservation = event_request.reservation
    if not reservation or reservation.status not in {"Held", "Reserved"}:
        return None
    reservation.status = "Released"
    reservation.released_at = datetime.utcnow()
    reservation.release_reason = reason
    reservation.hold_expires_at = None
    reservation.hold_token_hash = None
    reservation.draft_payload = None
    return reservation
