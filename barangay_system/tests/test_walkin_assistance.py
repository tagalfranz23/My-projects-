"""Real SQLite assisted intake and access checks; no live resident records."""
import base64
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

import test_system as baseline
from app import app
from models import (db, User, ResidentProfile, PermitApplication, EventRequest, BlotterCase,
                    Schedule, QueueEntry, Notification, ActivityLog, PasswordResetToken)
from services.revision import LOCAL_TIMEZONE
from migration import upgrade_legacy_schema


class WalkinAssistanceTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    post = baseline.SystemAcceptanceTests.post
    future_event_data = baseline.SystemAcceptanceTests.future_event_data
    setUpClass = classmethod(lambda cls: app.config.update(TESTING=True))

    def register(self, **extra):
        self.auth('staff')
        payload = dict(full_name='Offline Resident', address='22 Sample Street', purok_zone='Purok 3',
                       privacy_consent='1')
        payload.update(extra)
        response = self.post('/front-desk/residents/new', payload)
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            return User.query.filter_by(full_name=payload['full_name']).one().id

    def test_offline_registration_duplicate_detection_and_login_protection(self):
        resident_id = self.register()
        with app.app_context():
            resident = db.session.get(User, resident_id)
            self.assertFalse(resident.online_access_enabled)
            self.assertIsNone(resident.email)
            self.assertIsNone(resident.contact_number)
            self.assertEqual(resident.resident_profile.approval_status, 'Pending')
            self.assertEqual(resident.resident_profile.id_verification_status, 'Pending Verification')
            self.assertEqual(ActivityLog.query.filter_by(action='WALKIN_RESIDENT_REGISTERED').one().user_id, self.ids['staff'])
        self.post('/front-desk/residents/new', dict(full_name='Offline Resident', address='22 Sample Street',
                  purok_zone='Purok 3', privacy_consent='1'))
        with app.app_context():
            self.assertEqual(User.query.filter_by(full_name='Offline Resident').count(), 1)
            resident = db.session.get(User, resident_id)
            resident.set_password('KnownPassword123')
            username = resident.username
            resident.resident_profile.approval_status = 'Approved'
            raw, _ = PasswordResetToken.issue(resident)
            db.session.commit()
        self.auth(resident_id)
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        self.assertEqual(self.post('/login', {'username':username, 'password':'KnownPassword123'}).status_code, 200)
        self.assertEqual(self.client.get('/reset-password/'+raw).status_code, 302)

    def test_all_services_persist_queue_and_protect_blotter_slip(self):
        resident_id = self.register()
        response = self.post('/front-desk/requests', {'resident_id':resident_id, 'service_kind':'permit',
            'permit_type_id':self.ids['permit_type'], 'purpose':'Paper form request'})
        self.assertEqual(response.status_code, 302)
        event = self.future_event_data()
        event.update(resident_id=resident_id, service_kind='event')
        self.assertEqual(self.post('/front-desk/requests', event).status_code, 302)
        response = self.post('/front-desk/requests', {'resident_id':resident_id, 'service_kind':'blotter',
            'respondent_name':'Private Respondent', 'incident_type':'Complaint',
            'incident_date':'2026-01-01T09:00', 'incident_location':'Private Location', 'narrative':'Private Narrative'})
        self.assertEqual(response.status_code, 302)
        slip = self.client.get(response.location)
        self.assertEqual(slip.status_code, 200)
        self.assertIn(b'Print Acknowledgment', slip.data)
        self.assertIn(b'No mobile phone or online account', slip.data)
        for forbidden in [b'Private Respondent', b'Private Location', b'Private Narrative']:
            self.assertNotIn(forbidden, slip.data)
        with app.app_context():
            permit = PermitApplication.query.filter_by(applicant_id=resident_id).one()
            permit_id = permit.id
            self.assertEqual(permit.status, 'Pending')
            self.assertEqual(permit.encoded_by_staff_id, self.ids['staff'])
            self.assertEqual(EventRequest.query.one().status, 'Pending')
            self.assertEqual(BlotterCase.query.one().status, 'Filed')
        choices = self.client.get(f'/front-desk/residents/{resident_id}/transactions')
        self.assertEqual(len(choices.json), 3)
        appointment = (datetime.now(LOCAL_TIMEZONE)+timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')
        response = self.post('/front-desk/requests', {'resident_id':resident_id, 'service_kind':'schedule',
            'related_record':f'permit:{permit_id}', 'scheduled_datetime':appointment, 'purpose':'Manual follow-up'})
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            self.assertEqual(Schedule.query.one().status, 'Requested')
            tickets = QueueEntry.query.filter_by(resident_id=resident_id).all()
            self.assertEqual(len(tickets), 4)
            self.assertEqual(len({ticket.queue_number for ticket in tickets}), 4)
            self.assertTrue(all(ticket.created_by == self.ids['staff'] for ticket in tickets))
            self.assertEqual(Notification.query.filter_by(user_id=resident_id, channel='sms').count(), 0)
            self.assertEqual(Notification.query.filter_by(user_id=resident_id, channel='email').count(), 0)
            ticket_id = tickets[0].id
        for action in ['call','serve','complete']:
            self.assertEqual(self.post(f'/front-desk/queue/{ticket_id}/{action}', {}).status_code, 302)
        with app.app_context():
            ticket = db.session.get(QueueEntry, ticket_id)
            self.assertEqual(ticket.status, 'Completed')
            self.assertIsNotNone(ticket.completed_at)
            self.assertEqual(db.session.get(PermitApplication, permit_id).status, 'Pending')
        self.auth('resident')
        self.assertEqual(self.client.get(response.location).status_code, 403)
        self.assertEqual(self.client.get(f'/front-desk/residents/{resident_id}/transactions').status_code, 403)
        self.assertEqual(self.post('/front-desk/residents/new', {}).status_code, 403)

    def test_identity_scan_and_administrator_verification_are_required(self):
        resident_id = self.register()
        self.auth('admin')
        self.post(f'/users/{resident_id}/approval', {'action':'approve'})
        with app.app_context():
            self.assertEqual(db.session.get(User, resident_id).resident_profile.approval_status, 'Pending')
        self.auth('staff')
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        self.assertEqual(self.post(f'/front-desk/residents/{resident_id}/identity', {
            'accepted_id_type_id':self.ids['accepted_id'], 'valid_id_file':(BytesIO(image),'identity.png')}).status_code, 302)
        self.assertEqual(self.post(f'/users/{resident_id}/verification/id', {'action':'verify'}).status_code, 403)
        self.auth('admin')
        for kind in ['id','address']:
            self.post(f'/users/{resident_id}/verification/{kind}', {'action':'verify'})
        self.post(f'/users/{resident_id}/approval', {'action':'approve'})
        with app.app_context():
            resident = db.session.get(User, resident_id)
            self.assertEqual(resident.resident_profile.approval_status, 'Approved')
            self.assertFalse(resident.online_access_enabled)

    def test_invalid_input_conflicts_and_sms_simulation(self):
        resident_id = self.register(contact_number='09181112222')
        event = self.future_event_data()
        event.update(resident_id=resident_id, service_kind='event')
        self.post('/front-desk/requests', event)
        self.post('/front-desk/requests', event)
        with app.app_context():
            self.assertEqual(EventRequest.query.count(), 1)
            self.assertEqual(QueueEntry.query.count(), 1)
            self.assertEqual(Notification.query.filter_by(user_id=resident_id, channel='sms').one().status, 'simulated')
        self.post('/front-desk/requests', {'resident_id':resident_id, 'service_kind':'blotter',
            'incident_date':'invalid'})
        self.post('/front-desk/requests', {'resident_id':resident_id, 'service_kind':'schedule',
            'related_record':'event:999999', 'scheduled_datetime':'2030-01-01T09:00'})
        with app.app_context():
            self.assertEqual(BlotterCase.query.count(), 0)
            self.assertEqual(Schedule.query.count(), 0)
            self.assertEqual(QueueEntry.query.count(), 1)
            upgrade_legacy_schema()
            upgrade_legacy_schema()
            self.assertEqual(EventRequest.query.count(), 1)
        self.auth('resident')
        self.assertEqual(self.post('/front-desk/requests', event).status_code, 403)

    def test_concurrent_queue_numbers_are_unique(self):
        resident_id = self.register()
        def submit(_):
            with app.test_client() as client:
                with client.session_transaction() as session:
                    session['user_id'] = self.ids['staff']; session['csrf_token'] = 'parallel'
                return client.post('/front-desk/requests', data={'csrf_token':'parallel',
                    'resident_id':resident_id, 'service_kind':'permit',
                    'permit_type_id':self.ids['permit_type'], 'purpose':'Paper application'}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(submit, range(2))), [302,302])
        with app.app_context():
            self.assertEqual(PermitApplication.query.count(), 2)
            self.assertEqual(len({row.queue_number for row in QueueEntry.query.all()}), 2)

    def test_general_concern_removal_preserves_history_and_blotters(self):
        from models import GeneralConcern
        from services.analytics import REPORT_TYPES, build_report, validate_filters
        with app.app_context():
            concern = GeneralConcern(resident_id=self.ids['resident'], category='Historical concern',
                description='Retained historical record', encoded_by=self.ids['staff'])
            blotter = BlotterCase(complainant_id=self.ids['resident'], respondent_name='Private person',
                incident_type='Complaint', incident_date=datetime(2026,1,1),
                incident_location='Private location', narrative='Protected blotter narrative')
            db.session.add_all([concern, blotter]); db.session.commit()
            self.assertNotIn('concerns', REPORT_TYPES)
            sections = build_report(validate_filters({}))['sections']
            self.assertFalse(any('concern' in section['title'].lower() for section in sections))
        self.assertFalse(any(rule.rule.startswith('/front-desk/concerns') for rule in app.url_map.iter_rules()))
        for role in ['staff', 'admin']:
            self.auth(role)
            page = self.client.get('/front-desk')
            self.assertEqual(page.status_code, 200)
            for removed in [b'General Concern Intake', b'Recent Concerns', b'Record Concern',
                            b'/front-desk/concerns', b'Historical concern']:
                self.assertNotIn(removed, page.data)
            for retained in [b'New Walk-in Resident', b'Blotter Management', b'Event Approval', b'Scheduling / Appointments']:
                self.assertIn(retained, page.data)
            self.assertEqual(self.post('/front-desk/concerns', {}).status_code, 404)
            self.assertEqual(self.post('/front-desk/concerns/1/route', {}).status_code, 404)
            self.assertNotIn(b'Unresolved Concerns', self.client.get('/dashboard').data)
            self.assertEqual(self.client.get('/blotters').status_code, 200)
        with app.app_context():
            self.assertEqual(GeneralConcern.query.one().description, 'Retained historical record')
            self.assertEqual(BlotterCase.query.one().narrative, 'Protected blotter narrative')

    def test_out_of_scope_front_desk_features_removed_without_data_loss(self):
        from models import ResidentUpdateRequest, Household, HouseholdMember, AssistanceReferral
        from services.analytics import REPORT_TYPES, build_report, validate_filters
        with app.app_context():
            resident = db.session.get(User, self.ids['resident'])
            household = Household(household_number='Historical household', head_resident_id=resident.id)
            db.session.add(household); db.session.flush()
            db.session.add_all([
                HouseholdMember(household_id=household.id, resident_id=resident.id),
                ResidentUpdateRequest(resident_id=resident.id, field_name='email',
                    proposed_value='historical@example.invalid', created_by=self.ids['staff']),
                AssistanceReferral(resident_id=resident.id, category='Historical referral',
                    description='Preserved referral details', created_by=self.ids['staff'])])
            db.session.commit()
            resident_count = User.query.count()
            self.assertNotIn('households', REPORT_TYPES)
            self.assertFalse(any('household' in s['title'].lower() for s in build_report(validate_filters({}))['sections']))
        paths = ['/front-desk/resident-updates', '/resident-updates/1/review',
                 '/front-desk/households', '/front-desk/referrals']
        self.assertFalse(any(rule.endpoint in {'create_resident_update_request', 'review_resident_update',
            'manage_household', 'create_assistance_referral'} for rule in app.url_map.iter_rules()))
        for role in ['admin', 'staff']:
            self.auth(role)
            page = self.client.get('/front-desk')
            self.assertEqual(page.status_code, 200)
            for removed in [b'Resident Update Request', b'Household Management', b'Assistance Referral',
                            b'Recent Referrals', b'Approve Update', b'Reject Update']:
                self.assertNotIn(removed, page.data)
            for path in paths:
                self.assertNotIn(path.encode(), page.data)
                self.assertEqual(self.post(path, {}).status_code, 404)
            for retained in [b'New Walk-in Resident', b'Permit Processing', b'Event Approval',
                             b'Blotter Management', b'Scheduling / Appointments', b"Today's Queue"]:
                self.assertIn(retained, page.data)
        self.auth('resident')
        for path in ['/profile', '/permits', '/events', '/blotters', '/schedules', '/notifications']:
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertIn(b'Household Number', self.client.get('/profile').data)
        with app.app_context():
            self.assertEqual(User.query.count(), resident_count)
            self.assertEqual(Household.query.one().household_number, 'Historical household')
            self.assertEqual(HouseholdMember.query.count(), 1)
            self.assertEqual(ResidentUpdateRequest.query.one().status, 'Pending')
            self.assertEqual(AssistanceReferral.query.one().description, 'Preserved referral details')
            self.assertEqual(db.session.get(User, self.ids['resident']).email, 'resident@test.local')


if __name__ == '__main__':
    unittest.main()
