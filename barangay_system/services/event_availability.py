"""Centralized, transaction-safe event venue availability rules."""

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta, time

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
    # SQLite ignores SELECT FOR UPDATE; a no-op write serializes overlap checks.
    EventVenue.query.filter_by(id=venue_id).update({"is_active": EventVenue.is_active})
    query = db.session.query(EventVenue).filter(
        EventVenue.id == venue_id, EventVenue.is_active.is_(True)
    )
    venue = query.first()
    if not venue:
        raise ValueError("The selected venue is not available for requests.")
    return venue


def expire_stale_holds(moment=None):
    moment = moment or datetime.now(UTC).replace(tzinfo=None)
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


def legacy_event_intervals(venue_id, starts_at, ends_at):
    """Stored events without reservation rows still participate in availability."""
    venue = db.session.get(EventVenue, venue_id)
    if not venue:
        return []
    events = EventRequest.query.outerjoin(EventReservation).filter(
        EventReservation.id.is_(None),
        or_(EventRequest.venue_id == venue_id,
            and_(EventRequest.venue_id.is_(None), EventRequest.proposed_location == venue.name)),
        EventRequest.proposed_date >= starts_at.date(), EventRequest.proposed_date <= ends_at.date()).all()
    result = []
    for item in events:
        start = datetime.combine(item.proposed_date, item.proposed_start_time or time.min)
        end = datetime.combine(item.proposed_date, item.proposed_end_time) if item.proposed_end_time else datetime.combine(item.proposed_date + timedelta(days=1), time.min)
        if end <= start:
            end = datetime.combine(item.proposed_date + timedelta(days=1), time.min)
        if start < ends_at and end > starts_at:
            result.append((item, start, end))
    return result


def find_conflict(venue_id, starts_at, ends_at, exclude_reservation_id=None, lock=False, exclude_event_id=None):
    if not starts_at or not ends_at or ends_at <= starts_at:
        raise ValueError("Event end time must be later than its start time.")
    moment = datetime.now(UTC).replace(tzinfo=None)
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
    conflict = query.first()
    if conflict:
        return conflict
    return next((item for item, _, _ in legacy_event_intervals(venue_id, starts_at, ends_at)
                 if item.status in BLOCKING_EVENT_STATUSES and item.id != exclude_event_id), None)


def reschedule_event(item, venue_id, starts_at, ends_at):
    """Move the existing reservation atomically without changing approval status."""
    venue = _lock_venue(venue_id)
    db.session.refresh(item)
    if item.status not in BLOCKING_EVENT_STATUSES:
        raise ValueError('The event status changed. Reload before changing its schedule.')
    reservation = item.reservation
    if find_conflict(venue_id, starts_at, ends_at,
                     exclude_reservation_id=reservation.id if reservation else None, exclude_event_id=item.id):
        raise ReservationConflict('The selected venue and time are no longer available. Choose another slot.')
    if reservation is None:
        reservation = EventReservation(event_request=item, requester_id=item.requester_id, venue_id=venue_id,
                                       starts_at=starts_at, ends_at=ends_at, status='Reserved')
        db.session.add(reservation)
    reservation.venue_id, reservation.starts_at, reservation.ends_at = venue_id, starts_at, ends_at
    reservation.status = 'Reserved'
    reservation.released_at = reservation.release_reason = None
    item.venue_id, item.proposed_location = venue_id, venue.name
    item.proposed_date = starts_at.date()
    item.proposed_start_time, item.proposed_end_time = starts_at.time(), ends_at.time()


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
    if find_conflict(venue_id, starts_at, ends_at, lock=True, exclude_event_id=event_request.id):
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
        hold_expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=hold_minutes),
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
    if hold.hold_expires_at <= datetime.now(UTC).replace(tzinfo=None):
        hold.status = "Expired"
        hold.released_at = datetime.now(UTC).replace(tzinfo=None)
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
        exclude_event_id=event_request.id,
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
    hold.released_at = datetime.now(UTC).replace(tzinfo=None)
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
    reservation.released_at = datetime.now(UTC).replace(tzinfo=None)
    reservation.release_reason = reason
    reservation.hold_expires_at = None
    reservation.hold_token_hash = None
    reservation.draft_payload = None
    return reservation
