"""Regression coverage for the October 6 stabilization audit (isolated SQLite)."""
import base64
import threading
import unittest
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest.mock import patch

import test_system as baseline
import app as application
from app import app
from models import (ActivityLog, ApplicationRequirement, Notification, PermitType,
                    RequirementHistory, Schedule, ServiceRequirement, User, db)
from services.revision import LOCAL_TIMEZONE


class StabilizationTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    post = baseline.SystemAcceptanceTests.post
    clear_auth = baseline.SystemAcceptanceTests.clear_auth
    registration_data = baseline.SystemAcceptanceTests.registration_data
    create_permit = baseline.SystemAcceptanceTests.create_permit
    setUpClass = classmethod(lambda cls: app.config.update(TESTING=True))

    def pending_resident(self):
        self.clear_auth()
        data = self.registration_data('auditresident', 'auditresident@test.local')
        data['valid_id_file'] = (BytesIO(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
        )), 'identity.png')
        self.assertEqual(self.post('/register', data).status_code, 302)
        with app.app_context():
            identity = User.query.filter_by(username='auditresident').one().id
        self.auth('admin')
        return identity

    def test_missing_verification_action_never_approves(self):
        identity = self.pending_resident()
        self.post(f'/users/{identity}/verification/id')
        with app.app_context():
            self.assertEqual(db.session.get(User, identity).resident_profile.id_verification_status,
                             'Pending Verification')
            self.assertEqual(ActivityLog.query.filter_by(action='ID_VERIFIED').count(), 0)

    def test_private_id_preview_has_image_mimetype_and_no_cache(self):
        identity = self.pending_resident()
        with self.client.get(f'/resident-identifications/{identity}/document?inline=1') as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'image/png')
            self.assertTrue(response.headers['Content-Disposition'].startswith('inline'))
            self.assertIn('no-store', response.headers['Cache-Control'])
        self.auth('other')
        self.assertEqual(self.client.get(f'/resident-identifications/{identity}/document?inline=1').status_code, 403)

    def test_simultaneous_account_decisions_create_one_log_and_notice_set(self):
        identity = self.pending_resident()
        for kind in ('id', 'address'):
            self.post(f'/users/{identity}/verification/{kind}', {'verification_action': 'verify'})
        gate = threading.Barrier(2)
        readiness = application.approval_readiness

        def synchronized_readiness(profile):
            result = readiness(profile)
            gate.wait(timeout=10)
            return result

        def approve(_):
            with app.test_client() as client:
                with client.session_transaction() as session:
                    session['user_id'] = self.ids['admin']
                    session['csrf_token'] = 'audit-csrf'
                return client.post(f'/users/{identity}/approval', data={
                    'csrf_token': 'audit-csrf', 'approval_action': 'approve'
                }).status_code

        with patch.object(application, 'approval_readiness', synchronized_readiness):
            with ThreadPoolExecutor(max_workers=2) as workers:
                self.assertEqual(list(workers.map(approve, range(2))), [302, 302])
        with app.app_context():
            self.assertEqual(ActivityLog.query.filter_by(action='ACCOUNT_APPROVED').count(), 1)
            self.assertEqual(Notification.query.filter_by(user_id=identity, title='Resident account approved').count(), 3)

    def test_rejection_is_final_and_preserves_metadata(self):
        identity = self.pending_resident()
        self.post(f'/users/{identity}/approval', {'approval_action': 'reject', 'remarks': 'Front desk assistance'})
        with app.app_context():
            reviewed = db.session.get(User, identity).resident_profile.reviewed_at
        for action in ('reject', 'approve'):
            self.post(f'/users/{identity}/approval', {'approval_action': action})
        with app.app_context():
            profile = db.session.get(User, identity).resident_profile
            self.assertEqual(profile.approval_status, 'Rejected')
            self.assertEqual(profile.reviewed_at, reviewed)
            self.assertEqual(ActivityLog.query.filter_by(action='ACCOUNT_REJECTED').count(), 1)
        page = self.client.get(f'/users/{identity}').data
        self.assertNotIn(b'>Approve Account</button>', page)
        self.assertNotIn(b'>Reject Account</button>', page)


    def test_profile_changes_invalidate_only_changed_verification(self):
        self.auth('resident')
        self.post('/profile', {
            'full_name': 'Resident One', 'email': 'resident@test.local', 'contact_number': '09171234567',
            'house_number_street': 'New house', 'purok_zone': 'Zone 2', 'valid_id_number': 'REPLACED-NUMBER'
        })
        with app.app_context():
            profile = db.session.get(User, self.ids['resident']).resident_profile
            self.assertEqual(profile.address_verification_status, 'Pending Verification')
            self.assertEqual(profile.id_verification_status, 'Pending Verification')
            self.assertEqual(profile.approval_status, 'Approved')

    def test_front_desk_handles_service_without_configured_fee(self):
        with app.app_context():
            db.session.add(PermitType(name='Fee not set', fee=None, fee_is_configured=False))
            db.session.commit()
        self.auth('staff')
        self.assertEqual(self.client.get('/front-desk').status_code, 200)

    def test_admin_can_review_staff_address_correction(self):
        self.auth('staff')
        self.post('/profile', {'full_name': 'Staff Member', 'email': 'staff@test.local',
                              'house_number_street': 'Corrected staff house'})
        self.auth('admin')
        self.post(f"/users/{self.ids['staff']}/verification/address", {'verification_action': 'verify'})
        with app.app_context():
            self.assertEqual(db.session.get(User, self.ids['staff']).resident_profile.address_verification_status,
                             'Verified')

    def test_repeated_requirement_review_does_not_create_history(self):
        permit_id, _ = self.create_permit(status='Under Review')
        with app.app_context():
            definition = ServiceRequirement(permit_type_id=self.ids['permit_type'], name='Evidence',
                                            requirement_type='Text')
            db.session.add(definition)
            db.session.flush()
            requirement = ApplicationRequirement(application_id=permit_id, definition_id=definition.id, name='Evidence',
                                                requirement_type='Text', is_required=True, status='Submitted', value='Evidence')
            db.session.add(requirement)
            db.session.commit()
            requirement_id = requirement.id
        self.auth('staff')
        url = f'/permits/{permit_id}/requirements/{requirement_id}/review'
        self.assertEqual(self.post(url, {'status': 'Under Review'}).status_code, 302)
        self.assertEqual(self.post(url, {'status': 'Under Review'}).status_code, 409)
        with app.app_context():
            self.assertEqual(RequirementHistory.query.filter_by(requirement_id=requirement_id).count(), 1)

    def test_missing_id_file_cannot_be_verified(self):
        with app.app_context():
            profile = db.session.get(User, self.ids['resident']).resident_profile
            profile.id_verification_status = 'Pending Verification'
            db.session.commit()
        self.auth('admin')
        self.post(f"/users/{self.ids['resident']}/verification/id", {'verification_action': 'verify'})
        with app.app_context():
            self.assertEqual(db.session.get(User, self.ids['resident']).resident_profile.id_verification_status,
                             'Pending Verification')

    def test_dashboard_work_queue_keeps_older_actionable_requests(self):
        _, pending_reference = self.create_permit(status='Pending')
        _, endorsed_reference = self.create_permit(status='Endorsed to Admin')
        for _ in range(7):
            self.create_permit(status='Completed')
        self.auth('admin')
        self.assertIn(endorsed_reference.encode(), self.client.get('/dashboard').data)
        self.auth('staff')
        page = self.client.get('/dashboard').data
        self.assertIn(pending_reference.encode(), page)
        self.assertNotIn(b'No new requests are awaiting review.', page)

    def test_resident_dashboard_excludes_past_and_cancelled_appointments(self):
        permit_id, _ = self.create_permit()
        current_local = datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None)
        with app.app_context():
            db.session.add_all([
                Schedule(related_type='permit', related_id=permit_id, requested_by=self.ids['resident'],
                         scheduled_datetime=current_local-timedelta(hours=1), status='Confirmed', purpose='Past appointment'),
                Schedule(related_type='permit', related_id=permit_id, requested_by=self.ids['resident'],
                         scheduled_datetime=current_local+timedelta(hours=1), status='Cancelled', purpose='Cancelled appointment'),
            ])
            db.session.commit()
        self.auth('resident')
        page = self.client.get('/dashboard').data
        self.assertNotIn(b'Past appointment', page)
        self.assertNotIn(b'Cancelled appointment', page)
