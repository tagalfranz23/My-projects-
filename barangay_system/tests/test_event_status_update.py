"""Event completion rules, transaction rollback and duplicate-submit protection."""
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest.mock import patch
from sqlalchemy.exc import OperationalError, SQLAlchemyError
import test_system as baseline
import app as application
from app import app
from models import db, EventRequest, Payment, Notification, ActivityLog
from services.revision import payment_status


class EventStatusUpdateTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    post = baseline.SystemAcceptanceTests.post
    setUpClass = classmethod(lambda cls: app.config.update(TESTING=True))

    def event(self, fee=100):
        self.auth('admin')
        with app.app_context():
            item = EventRequest(requester_id=self.ids['resident'], event_name='Completion regression',
                description='Synthetic test event', proposed_date=date.today()+timedelta(days=30),
                proposed_location='Test venue', status='Approved', fee_at_submission=fee)
            db.session.add(item)
            db.session.commit()
            return item.id

    def notifications(self, event_id):
        return Notification.query.filter_by(related_type='event', related_id=event_id,
                                            title='Event status updated').all()

    def complete(self, event_id, **extra):
        return self.post(f'/events/{event_id}', dict(status='Completed', expected_status='Approved', **extra))

    def test_unpaid_completion_preserves_payment_status_and_review_remarks(self):
        event_id = self.event()
        page = self.client.get(f'/events/{event_id}')
        self.assertNotIn(b'eventPaymentRequirement', page.data)
        self.assertNotIn(b'payment required', page.data)
        self.assertIn(b'>Completed</option>', page.data)
        response = self.complete(event_id, remarks='Preserve my review text')
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            item = db.session.get(EventRequest,event_id)
            self.assertEqual(item.status, 'Completed')
            self.assertEqual(item.remarks, 'Preserve my review text')
            self.assertEqual(item.reviewed_by, self.ids['admin'])
            self.assertEqual(payment_status(item), 'Unpaid')
            self.assertEqual(Payment.query.count(), 0)
            self.assertEqual(len(self.notifications(event_id)), 3)
            self.assertEqual(ActivityLog.query.filter_by(action='EVENT_STATUS_CHANGED').count(), 1)

    def test_all_payment_states_complete_once_without_changing_payment_records(self):
        for payment in ['Unpaid', 'Paid', 'Waived', 'Not Required']:
            with self.subTest(payment=payment):
                event_id = self.event(fee=0 if payment=='Not Required' else 100)
                if payment in {'Paid', 'Waived'}:
                    response = self.post('/payments',dict(kind='event',request_id=str(event_id),
                        status=payment,amount='100' if payment=='Paid' else '0',method='Cash',
                        notes='Authorized test waiver' if payment=='Waived' else ''))
                    self.assertEqual(response.status_code,302)
                with app.app_context():
                    payment_before = [tuple(getattr(row, column.name) for column in Payment.__table__.columns)
                                      for row in Payment.query.order_by(Payment.id).all()]
                self.assertEqual(self.complete(event_id).status_code,302)
                with app.app_context():
                    self.assertEqual(db.session.get(EventRequest,event_id).status,'Completed')
                    self.assertEqual(payment_status(db.session.get(EventRequest,event_id)),payment)
                    payment_after = [tuple(getattr(row, column.name) for column in Payment.__table__.columns)
                                     for row in Payment.query.order_by(Payment.id).all()]
                    self.assertEqual(payment_before,payment_after)
                    notifications = self.notifications(event_id)
                    ids = [item.id for item in notifications]
                    self.assertEqual({item.channel for item in notifications}, {'in_app','email','sms'})
                    reference = db.session.get(EventRequest,event_id).reference_no
                    self.assertEqual(ActivityLog.query.filter_by(action='EVENT_STATUS_CHANGED',target_reference=reference).count(),1)
                duplicate = self.complete(event_id)
                self.assertEqual(duplicate.status_code,409)
                self.assertIn(b'already changed',duplicate.data)
                self.assertEqual(self.client.get(f'/events/{event_id}').status_code,200)
                with app.app_context():
                    self.assertEqual([item.id for item in self.notifications(event_id)],ids)

    def test_unpaid_cancellation_still_allowed_and_invalid_or_stale_rejected(self):
        event_id = self.event()
        self.assertEqual(self.post(f'/events/{event_id}',dict(status='Cancelled',expected_status='Pending')).status_code,409)
        self.assertEqual(self.post(f'/events/{event_id}',dict(status='Approved')).status_code,409)
        for data in [{}, dict(status='Invalid')]:
            self.assertEqual(self.post(f'/events/{event_id}',data).status_code,400)
        self.auth('staff')
        self.assertEqual(self.complete(event_id).status_code,400)
        self.auth('resident')
        self.assertEqual(self.complete(event_id).status_code,403)
        self.auth('admin')
        self.assertEqual(self.post(f'/events/{event_id}',dict(status='Cancelled',expected_status='Approved')).status_code,302)
        with app.app_context():
            self.assertEqual(db.session.get(EventRequest,event_id).status,'Cancelled')
            self.assertEqual(Payment.query.count(),0)

    def test_failure_after_staging_notifications_rolls_back_everything(self):
        event_id = self.event(fee=0)
        real_queue = application.queue_notifications
        def fail_after_flush(*args, **kwargs):
            real_queue(*args, **kwargs)
            db.session.flush()
            raise SQLAlchemyError('synthetic private SQL parameters must never be logged')
        with patch('app.queue_notifications',side_effect=fail_after_flush), self.assertLogs(app.logger,level='ERROR') as logs:
            response = self.complete(event_id)
        self.assertEqual(response.status_code,500)
        self.assertIn(b'No changes or notifications were saved',response.data)
        self.assertNotIn('private SQL parameters',' '.join(logs.output))
        with app.app_context():
            self.assertEqual(db.session.get(EventRequest,event_id).status,'Approved')
            self.assertEqual(self.notifications(event_id),[])
            self.assertEqual(ActivityLog.query.filter_by(action='EVENT_STATUS_CHANGED').count(),0)
        self.assertEqual(self.complete(event_id).status_code,302)

    def test_database_busy_has_specific_safe_conflict_response(self):
        event_id = self.event(fee=0)
        original = sqlite3.OperationalError('database is locked')
        original.sqlite_errorcode = sqlite3.SQLITE_BUSY
        error = OperationalError('synthetic SQL',{},original)
        with patch('app.update_status',side_effect=error), self.assertLogs(app.logger,level='ERROR'):
            response = self.complete(event_id)
        self.assertEqual(response.status_code,409)
        self.assertIn(b'database is busy',response.data)
        with app.app_context():
            self.assertEqual(db.session.get(EventRequest,event_id).status,'Approved')
            self.assertEqual(self.notifications(event_id),[])

    def test_two_simultaneous_submissions_emit_one_notification_set(self):
        event_id = self.event()
        barrier = threading.Barrier(2)
        validate = application.validate_transition
        def simultaneous(*args):
            result = validate(*args)
            barrier.wait(timeout=10)
            return result
        def submit():
            with app.test_client() as client:
                with client.session_transaction() as session:
                    session['user_id']=self.ids['admin'];session['csrf_token']='parallel-test'
                return client.post(f'/events/{event_id}',data=dict(status='Completed',expected_status='Approved',csrf_token='parallel-test')).status_code
        with patch('app.validate_transition',side_effect=simultaneous), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:submit(),range(2)))
        self.assertEqual(sorted(results),[302,409])
        with app.app_context():
            self.assertEqual(db.session.get(EventRequest,event_id).status,'Completed')
            self.assertEqual(payment_status(db.session.get(EventRequest,event_id)),'Unpaid')
            self.assertEqual(Payment.query.count(),0)
            self.assertEqual(len(self.notifications(event_id)),3)
            self.assertEqual(ActivityLog.query.filter_by(action='EVENT_STATUS_CHANGED').count(),1)


if __name__=='__main__':
    unittest.main()
