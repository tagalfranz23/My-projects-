"""Authenticated calendar read access and administrator event schedule changes."""
from datetime import datetime
from flask import abort, jsonify, request, flash, redirect, url_for
from models import db, EventVenue, EventRequest
from services.event_calendar import month_availability
from services.event_availability import reschedule_event, ReservationConflict, BLOCKING_EVENT_STATUSES
from services.revision import LOCAL_TIMEZONE


def register_event_calendar_routes(app, login_required, actor, audit, notify):
    @app.get('/events/availability')
    @login_required
    def event_calendar_availability():
        month=request.args.get('month') or datetime.now(LOCAL_TIMEZONE).strftime('%Y-%m')
        try:
            value=datetime.strptime(month+'-01','%Y-%m-%d')
            if len(month)!=7 or not 1900<=value.year<=9998: raise ValueError()
            venue_id=request.args.get('venue_id',type=int)
            if not venue_id or not 1<=venue_id<=2147483647: raise ValueError()
            venue=db.session.get(EventVenue,venue_id)
            if not venue or not venue.is_active: raise ValueError()
            a,b=request.args.get('start_time',''),request.args.get('end_time','')
            starts=ends=None
            if a or b:
                starts=datetime.strptime(a,'%H:%M').time();ends=datetime.strptime(b,'%H:%M').time()
                if ends<=starts: raise ValueError()
        except (ValueError,TypeError):
            abort(400,'Choose an active venue, a valid month, and an end time after the start time.')
        response=jsonify(month_availability(venue,month,starts,ends,actor()))
        response.headers['Cache-Control']='private, no-store'
        return response

    @app.post('/events/<int:record_id>/reschedule')
    @login_required
    def event_reschedule(record_id):
        if actor().role!='admin': abort(403)
        item=EventRequest.query.get_or_404(record_id)
        if item.status not in BLOCKING_EVENT_STATUSES:
            abort(409,'Only an active event request can be rescheduled.')
        try:
            event_date=datetime.strptime(request.form.get('proposed_date',''),'%Y-%m-%d').date()
            starts=datetime.combine(event_date,datetime.strptime(request.form.get('proposed_start_time',''),'%H:%M').time())
            ends=datetime.combine(event_date,datetime.strptime(request.form.get('proposed_end_time',''),'%H:%M').time())
            venue_id=request.form.get('venue_id',type=int)
            if not venue_id or not 1<=venue_id<=2147483647 or ends<=starts or starts<=datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None):
                raise ValueError('Choose a future date, active venue and valid time range.')
            reschedule_event(item,venue_id,starts,ends)
            audit('EVENT_RESCHEDULED','event',item.reference_no,'Venue/time updated; approval status unchanged')
            notify(item.requester_id,'Event schedule updated',f'{item.reference_no}: schedule changed to {starts:%b %d, %Y %H:%M}.','event',item.id)
            db.session.commit()
            flash('Event schedule updated. Calendar availability has been refreshed.','success')
        except (ValueError,ReservationConflict) as exc:
            db.session.rollback();flash(str(exc),'warning')
        return redirect(url_for('event_detail',record_id=item.id))
