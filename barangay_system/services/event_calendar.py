"""Venue-specific calendar snapshots derived from existing event/reservation rows."""
import calendar
from datetime import datetime, timedelta, time
from sqlalchemy.orm import joinedload
from models import EventRequest, EventReservation
from services.event_availability import BLOCKING_EVENT_STATUSES, legacy_event_intervals
from services.revision import LOCAL_TIMEZONE


def month_availability(venue, month, starts, ends, viewer):
    start = datetime.strptime(month+'-01','%Y-%m-%d')
    finish = (start.replace(day=28)+timedelta(days=4)).replace(day=1)
    now = datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None)
    # Holds are stored in UTC by the existing reservation engine.
    from models import utcnow
    utc = utcnow()
    intervals = []
    for reservation in EventReservation.query.options(joinedload(EventReservation.event_request)).filter(
            EventReservation.venue_id==venue.id, EventReservation.starts_at < finish,
            EventReservation.ends_at > start).all():
        item = reservation.event_request
        held = reservation.status=='Held' and reservation.hold_expires_at and reservation.hold_expires_at > utc
        blocking = bool(held or (reservation.status=='Reserved' and item and item.status in BLOCKING_EVENT_STATUSES))
        if held or item:
            intervals.append((item,reservation.starts_at,reservation.ends_at,blocking, bool(held)))
    intervals.extend((item,a,b,item.status in BLOCKING_EVENT_STATUSES,False)
                     for item,a,b in legacy_event_intervals(venue.id,start,finish))
    days=[]
    for day in range(1,calendar.monthrange(start.year,start.month)[1]+1):
        date=start.replace(day=day); next_day=date+timedelta(days=1)
        rows=[row for row in intervals if row[1]<next_day and row[2]>date]
        blockers=[row for row in rows if row[3]]
        requested_start=datetime.combine(date.date(),starts) if starts else date
        requested_end=datetime.combine(date.date(),ends) if ends else next_day
        overlaps=[row for row in blockers if row[1]<requested_end and row[2]>requested_start]
        if starts:
            available=not overlaps and requested_start>now
        else:
            cursor=max(date,now)
            available=False
            for _,a,b,_,_ in sorted(blockers,key=lambda row:row[1]):
                if a>cursor: available=True
                cursor=max(cursor,b)
            available=available or cursor<next_day
        approved=any(row[0] and row[0].status=='Approved' for row in overlaps)
        pending=any(row[0] and row[0].status in BLOCKING_EVENT_STATUSES-{'Approved'} for row in overlaps)
        state='available'
        if not available:
            state='booked' if approved else ('pending' if pending else 'unavailable')
        elif blockers:
            state='partial'
        details=[]
        for item,a,b,blocking,held in rows:
            permitted=bool(item and (viewer.role in {'admin','staff'} or item.requester_id==viewer.id))
            if not blocking and not permitted: continue
            detail={'start':a.isoformat(timespec='minutes'),'end':b.isoformat(timespec='minutes'),
                    'status':item.status if permitted else ('Booked' if item and item.status=='Approved' else 'Pending reservation' if not held else 'Temporary hold'),
                    'blocking':blocking}
            if permitted:
                detail.update(name=item.event_name, reference=item.reference_no, id=item.id)
            details.append(detail)
        days.append({'date':date.date().isoformat(),'day':day,'available':bool(available),'state':state,
                     'booked':sum(1 for row in blockers if row[0] and row[0].status=='Approved'),
                     'pending':sum(1 for row in blockers if row[0] and row[0].status!='Approved'),
                     'details':details})
    return {'month':month,'venue_id':venue.id,'venue':venue.name,'days':days,
            'weekday':(start.weekday()+1)%7,'interval_selected':bool(starts)}
