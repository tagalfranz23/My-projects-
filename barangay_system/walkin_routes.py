"""Authorized assisted intake using existing resident, workflow and queue records."""
import secrets
import re
from datetime import date, datetime

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError

from models import (db, utcnow, User, ResidentProfile, PermitApplication, EventRequest,
                    BlotterCase, Schedule, QueueEntry, AcceptedIdType)
from permissions import permission_required
from services.event_availability import reserve_event, ReservationConflict
from services.revision import LOCAL_TIMEZONE
from services.resident_family import DISABILITY_TYPES, validate_family, save_family, form_family_rows


def register_walkin_routes(app, login_required, actor, audit, save_id, save_upload,
                           notify, next_number, get_resident, parse_event, permit_intake):
    def lock_writer():
        db.session.execute(text("UPDATE users SET updated_at=updated_at WHERE id=:id"), {"id": actor().id})

    def field(name, limit, required=False):
        value = request.form.get(name, '').strip()
        if len(value) > limit or (required and not value):
            raise ValueError(f"Complete {name.replace('_', ' ')} using at most {limit} characters.")
        return value

    def identity_evidence(profile):
        upload = request.files.get('valid_id_file')
        if upload and upload.filename:
            kind = db.session.get(AcceptedIdType, request.form.get('accepted_id_type_id', type=int))
            if not kind or not kind.is_active or not kind.is_accepted_for_registration:
                raise ValueError('Select an accepted ID type for the scanned document.')
            profile.accepted_id_type_id = kind.id
            profile.valid_id_number = field('valid_id_number', 120) or None
            profile.valid_id_file_path = save_id()
            profile.id_verification_status = 'Pending Verification'
            profile.id_verified_by = profile.id_verified_at = profile.id_rejection_reason = None

    @app.route('/front-desk/residents/new', methods=['GET', 'POST'])
    @login_required
    @permission_required('front_desk', 'manage')
    def new_walkin_resident():
        if request.method == 'POST':
            try:
                name = ' '.join(field('full_name', 150, True).split())
                address = ' '.join(field('address', 180, True).split())
                purok = field('purok_zone', 120, True)
                phone = re.sub(r'[\s()-]', '', field('contact_number', 20))
                if phone and not re.fullmatch(r'\+?[0-9]{10,15}', phone):
                    raise ValueError('Enter a valid mobile number, or leave it blank.')
                birthday = date.fromisoformat(request.form['date_of_birth']) if request.form.get('date_of_birth') else None
                if birthday and birthday >= datetime.now(LOCAL_TIMEZONE).date():
                    raise ValueError('Enter a date of birth in the past.')
                if request.form.get('privacy_consent') != '1':
                    raise ValueError('Explain the privacy notice and record the resident’s consent.')
                family_data = validate_family(request.form)
                lock_writer()
                # A shared phone may belong to a household: present candidates rather than merge.
                same_name = func.lower(func.trim(User.full_name)) == name.lower()
                same_address = func.lower(func.trim(ResidentProfile.house_number_street)) == address.lower()
                matches = User.query.join(ResidentProfile, ResidentProfile.user_id == User.id).filter(
                    User.role.in_(('resident', 'staff')), or_(
                        same_name & same_address,
                        same_name & (ResidentProfile.date_of_birth == birthday) if birthday else same_name & same_address,
                        User.contact_number == phone if phone else User.id == -1)).all()
                if matches:
                    db.session.rollback()
                    flash('Possible existing resident found. Check the search results and assist the correct resident instead of creating a duplicate.', 'warning')
                    return redirect(url_for('front_desk', q=phone or name))
                resident = User(username='walkin-' + secrets.token_hex(12), full_name=name,
                                address=address, contact_number=phone or None, email=None,
                                role='resident', is_active=True, online_access_enabled=False)
                resident.set_password(secrets.token_urlsafe(48))
                db.session.add(resident)
                db.session.flush()
                profile = ResidentProfile(user_id=resident.id, house_number_street=address,
                    purok_zone=purok, date_of_birth=birthday, privacy_consent=True,
                    privacy_consented_at=utcnow(), approval_status='Pending')
                identity_evidence(profile)
                db.session.add(profile)
                db.session.flush()
                save_family(profile, family_data, actor().id)
                audit('WALKIN_RESIDENT_REGISTERED', 'users', str(resident.id), 'Offline resident; verification pending')
                db.session.commit()
                flash('Resident recorded. You can now encode the request. Administrator verification is still required.', 'success')
                return redirect(url_for('front_desk', resident_id=resident.id))
            except (ValueError, IntegrityError) as exc:
                db.session.rollback()
                flash(str(exc) if isinstance(exc, ValueError) else 'The resident could not be saved. Search for an existing record and try again.', 'danger')
        response = app.make_response(render_template('walkin_resident.html', id_types=AcceptedIdType.query.filter_by(
            is_active=True, is_accepted_for_registration=True).order_by(AcceptedIdType.name).all(),
            disability_types=DISABILITY_TYPES, family_rows=form_family_rows(request.form), profile=None))
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @app.route('/front-desk/residents/<int:resident_id>/information', methods=['GET', 'POST'])
    @login_required
    @permission_required('front_desk', 'manage')
    def resident_information(resident_id):
        resident = get_resident(resident_id)
        if not resident:
            abort(404)
        profile = resident.resident_profile
        if request.method == 'POST':
            try:
                if request.form.get('family_information') != '1':
                    raise ValueError('Complete the family and disability information form.')
                lock_writer()
                data = validate_family(request.form, profile)
                save_family(profile, data, actor().id)
                audit('RESIDENT_FAMILY_INFORMATION_UPDATED', 'resident_profile', str(resident.id),
                      'Confidential resident details updated')
                db.session.commit()
                flash('Resident family and disability information saved.', 'success')
                return redirect(url_for('resident_information', resident_id=resident.id))
            except (ValueError, IntegrityError) as exc:
                db.session.rollback()
                flash(str(exc) if isinstance(exc, ValueError) else
                      'A duplicate or changed family record was detected. Reload and check the entries.', 'danger')
        rows = form_family_rows(request.form) if request.method == 'POST' else profile.family_members
        response = app.make_response(render_template('resident_information.html', resident=resident,
            profile=profile, family_rows=rows, disability_types=DISABILITY_TYPES))
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @app.post('/front-desk/residents/<int:resident_id>/identity')
    @login_required
    @permission_required('front_desk', 'manage')
    def walkin_identity(resident_id):
        resident = get_resident(resident_id)
        if not resident or resident.online_access_enabled:
            abort(404)
        try:
            if not request.files.get('valid_id_file') or not request.files['valid_id_file'].filename:
                raise ValueError('Choose a scanned ID document.')
            identity_evidence(resident.resident_profile)
            audit('WALKIN_ID_ENCODED', 'resident_profile', str(resident_id), 'Scanned evidence; administrator verification pending')
            db.session.commit()
            flash('ID saved for Administrator verification.', 'success')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        return redirect(url_for('front_desk', resident_id=resident_id))

    @app.post('/front-desk/requests')
    @login_required
    @permission_required('front_desk', 'manage')
    def assisted_request():
        kind = request.form.get('service_kind', 'permit')
        if kind == 'permit':
            return permit_intake()
        resident = get_resident(request.form.get('resident_id', type=int))
        if not resident or not resident.is_active or resident.resident_profile.approval_status == 'Rejected':
            abort(400, 'Select an eligible resident record.')
        if kind not in {'event', 'blotter', 'schedule'}:
            abort(400, 'Select a supported service.')
        try:
            lock_writer()
            if kind == 'event':
                field('event_name', 180, True)
                field('description', 10000, True)
                category, venue, name, description, starts, ends = parse_event()
                item = EventRequest(requester_id=resident.id, event_name=name, description=description,
                    category_id=category.id, venue_id=venue.id, event_type=category.name,
                    proposed_date=starts.date(), proposed_start_time=starts.time(), proposed_end_time=ends.time(),
                    proposed_location=venue.name, expected_attendees=0, fee_at_submission=category.fee)
                db.session.add(item)
                db.session.flush()
                reserve_event(item, venue.id, starts, ends)
            elif kind == 'blotter':
                incident = datetime.fromisoformat(request.form.get('incident_date', ''))
                if incident.tzinfo is not None or incident > datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None):
                    raise ValueError('Enter a valid incident date and time that is not in the future.')
                item = BlotterCase(complainant_id=resident.id,
                    respondent_name=field('respondent_name', 150, True),
                    respondent_address=field('respondent_address', 255) or None,
                    incident_type=field('incident_type', 120, True), incident_date=incident,
                    incident_location=field('incident_location', 255, True),
                    narrative=field('narrative', 10000, True), supporting_document_path=save_upload('attachment'))
                db.session.add(item)
            else:
                related_kind, raw_id = request.form.get('related_record', '').split(':', 1)
                model, owner_field = {'permit': (PermitApplication, 'applicant_id'),
                    'event': (EventRequest, 'requester_id'), 'blotter': (BlotterCase, 'complainant_id')}[related_kind]
                linked_id = int(raw_id)
                if not 1 <= linked_id <= 2147483647:
                    raise ValueError('Choose a valid resident transaction.')
                linked = db.session.get(model, linked_id)
                if not linked or getattr(linked, owner_field) != resident.id:
                    raise ValueError('Choose a transaction belonging to this resident.')
                scheduled = datetime.fromisoformat(request.form.get('scheduled_datetime', ''))
                if scheduled.tzinfo is not None or scheduled <= datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None):
                    raise ValueError('Choose a future appointment date and time.')
                item = Schedule(requested_by=resident.id, related_type=related_kind, related_id=linked.id,
                    scheduled_datetime=scheduled, purpose=field('purpose', 255, True))
                db.session.add(item)
            db.session.flush()
            category_name = {'event': 'Event Request', 'blotter': 'Complaint / Concern', 'schedule': 'Appointment'}[kind]
            ticket = QueueEntry(queue_date=datetime.now(LOCAL_TIMEZONE).date(), queue_number=next_number(category_name),
                category=category_name, resident_id=resident.id, related_type=kind, related_id=item.id, created_by=actor().id)
            db.session.add(ticket)
            db.session.flush()
            reference = item.case_no if kind == 'blotter' else item.reference_no
            # Audit and notification summaries deliberately exclude incident narratives.
            audit('WALKIN_REQUEST_CREATED', kind, reference, 'Staff-assisted / paper intake')
            audit('QUEUE_CREATED', 'queue', ticket.queue_number, reference)
            notify(resident.id, 'Walk-in request received', f'{reference} was recorded. Bring your printed reference to the Barangay front desk for follow-up.', kind, item.id)
            db.session.commit()
            return redirect(url_for('assisted_acknowledgment', queue_id=ticket.id))
        except ReservationConflict:
            db.session.rollback()
            flash('That venue and time are unavailable. Choose another time; no request or ticket was saved.', 'warning')
        except (ValueError, TypeError, KeyError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc) if isinstance(exc, ValueError) else 'Check the service details and try again. No request was saved.', 'danger')
        return redirect(url_for('front_desk', resident_id=resident.id))

    @app.get('/front-desk/residents/<int:resident_id>/transactions')
    @login_required
    @permission_required('front_desk', 'view')
    def walkin_transactions(resident_id):
        from flask import jsonify
        if not get_resident(resident_id):
            abort(404)
        choices = []
        tickets = {(ticket.related_type or 'permit', ticket.related_id or ticket.permit_application_id): ticket
                   for ticket in QueueEntry.query.filter_by(resident_id=resident_id).order_by(QueueEntry.id).all()}
        for kind, model, owner in [('permit', PermitApplication, 'applicant_id'),
                                    ('event', EventRequest, 'requester_id'), ('blotter', BlotterCase, 'complainant_id'),
                                    ('schedule', Schedule, 'requested_by')]:
            for item in model.query.filter(getattr(model, owner) == resident_id).order_by(model.id.desc()).all():
                reference = item.case_no if kind == 'blotter' else item.reference_no
                ticket = tickets.get((kind, item.id))
                choices.append({'value': f'{kind}:{item.id}', 'label': f'{reference} — {item.status}',
                    'schedulable': kind != 'schedule',
                    'slip_url': url_for('assisted_acknowledgment', queue_id=ticket.id) if ticket else None})
        response = jsonify(choices)
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @app.get('/front-desk/queue/<int:queue_id>/acknowledgment')
    @login_required
    @permission_required('front_desk', 'view')
    def assisted_acknowledgment(queue_id):
        ticket = QueueEntry.query.get_or_404(queue_id)
        kind = ticket.related_type or ('permit' if ticket.permit_application_id else None)
        model = {'permit': PermitApplication, 'event': EventRequest, 'blotter': BlotterCase, 'schedule': Schedule}.get(kind)
        item = db.session.get(model, ticket.related_id or ticket.permit_application_id) if model else None
        if not item:
            abort(404)
        reference = item.case_no if kind == 'blotter' else item.reference_no
        audit('WALKIN_ACKNOWLEDGMENT_GENERATED', kind, reference)
        db.session.commit()
        response = app.make_response(render_template('assisted_acknowledgment.html', queue=ticket,
            reference=reference, status=item.status))
        response.headers['Cache-Control'] = 'private, no-store'
        return response
