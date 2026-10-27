"""Permit pickup conflict reasons, persistence, rollback and concurrent retries."""
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from sqlalchemy.exc import OperationalError, SQLAlchemyError
import test_system as baseline
import app as application
from app import app
from services.revision import payment_status
from models import (db, PermitApplication, Payment, ServiceRequirement,
                    ApplicationRequirement, Notification, ActivityLog)


class PermitStatusConflictTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    post = baseline.SystemAcceptanceTests.post
    create_permit = baseline.SystemAcceptanceTests.create_permit
    setUpClass = classmethod(lambda cls:app.config.update(TESTING=True))

    def permit(self, fee=1500):
        permit_id,_=self.create_permit(status='Approved',fee=fee)
        self.auth('admin')
        return permit_id

    def prepare(self,permit_id,**values):
        data=dict(status='Ready for Pickup',expected_status='Approved',remarks='Preserve submitted review remarks')
        data.update(values)
        return self.post(f'/permits/{permit_id}',data)

    def notices(self,permit_id):
        return Notification.query.filter_by(related_type='permit',related_id=permit_id,title='Permit status updated').all()

    def test_unpaid_permit_prepares_downloads_and_releases_without_payment(self):
        permit_id=self.permit()
        page=self.client.get(f'/permits/{permit_id}')
        self.assertNotIn(b'Payment is Unpaid for the recorded fee',page.data)
        self.assertNotIn(b'authorized payment recorder must',page.data)
        self.assertNotIn(b'permitPickupRequirements',page.data)
        response=self.prepare(permit_id)
        self.assertEqual(response.status_code,200)
        with app.app_context():
            item=db.session.get(PermitApplication,permit_id)
            self.assertEqual(item.status,'Ready for Pickup')
            self.assertEqual(item.remarks,'Preserve submitted review remarks')
            self.assertEqual(payment_status(item),'Unpaid')
            self.assertEqual(Payment.query.count(),0)
            self.assertEqual(len(self.notices(permit_id)),3)
            self.assertEqual(ActivityLog.query.filter_by(action='PERMIT_READY_FOR_RELEASE').count(),1)
        self.assertEqual(self.client.get(f'/permits/{permit_id}/approved-document.docx').status_code,200)
        self.assertEqual(self.client.get(f'/acknowledgments/permit/{permit_id}.docx').status_code,200)
        self.assertEqual(self.post(f'/front-desk/permits/{permit_id}/release').status_code,302)
        with app.app_context():self.assertEqual(db.session.get(PermitApplication,permit_id).status,'Ready for Pickup')
        self.assertEqual(self.post(f'/front-desk/permits/{permit_id}/release',dict(identity_confirmed='1')).status_code,302)
        with app.app_context():
            item=db.session.get(PermitApplication,permit_id)
            self.assertEqual(item.status,'Completed')
            self.assertEqual(payment_status(item),'Unpaid')
            self.assertEqual(Payment.query.count(),0)

    def test_required_verification_names_and_statuses_are_displayed(self):
        permit_id=self.permit()
        with app.app_context():
            for name,status in [('Proof of address','Not Submitted'),('Supporting evidence','Submitted')]:
                definition=ServiceRequirement(permit_type_id=self.ids['permit_type'],name=name,requirement_type='Text',is_required=True)
                db.session.add(definition);db.session.flush()
                db.session.add(ApplicationRequirement(application_id=permit_id,definition_id=definition.id,
                    name=name,requirement_type='Text',is_required=True,status=status))
            db.session.commit()
        response=self.prepare(permit_id)
        self.assertEqual(response.status_code,409)
        self.assertIn(b'Proof of address (Not Submitted)',response.data)
        self.assertIn(b'Supporting evidence (Submitted)',response.data)
        with app.app_context():
            for row in ApplicationRequirement.query.all():row.status='Verified'
            db.session.commit()
        self.assertEqual(self.prepare(permit_id).status_code,200)

    def test_paid_transition_saves_remarks_and_deduplicates_retries(self):
        permit_id=self.permit()
        self.assertEqual(self.post('/payments',dict(kind='permit',request_id=permit_id,amount='1500')).status_code,302)
        with app.app_context():
            payment_before=tuple(getattr(Payment.query.one(),column.name) for column in Payment.__table__.columns)
        self.assertEqual(self.prepare(permit_id).status_code,200)
        with app.app_context():
            item=db.session.get(PermitApplication,permit_id)
            self.assertEqual(item.status,'Ready for Pickup')
            self.assertEqual(item.remarks,'Preserve submitted review remarks')
            self.assertEqual(item.reviewed_by,self.ids['admin'])
            self.assertFalse(item.is_signed)
            ids=[row.id for row in self.notices(permit_id)]
            self.assertEqual(len(ids),3)
        self.assertEqual(self.prepare(permit_id).status_code,409)
        self.assertEqual(self.client.get(f'/permits/{permit_id}').status_code,200)
        with app.app_context():
            self.assertEqual(ids,[row.id for row in self.notices(permit_id)])
            self.assertEqual(payment_before,tuple(getattr(Payment.query.one(),column.name) for column in Payment.__table__.columns))

    def test_invalid_stale_and_unauthorized_requests_do_not_update(self):
        permit_id=self.permit(fee=0)
        self.assertEqual(self.prepare(permit_id,expected_status='Pending').status_code,409)
        self.assertEqual(self.prepare(permit_id,status='Invalid').status_code,400)
        self.auth('resident');self.assertEqual(self.prepare(permit_id).status_code,403)
        self.auth('staff');self.assertEqual(self.prepare(permit_id).status_code,400)
        with app.app_context():
            self.assertEqual(db.session.get(PermitApplication,permit_id).status,'Approved')
            self.assertEqual(self.notices(permit_id),[])

    def test_database_failure_rolls_back_status_audit_and_notifications(self):
        permit_id=self.permit(fee=0)
        real_notify=application.queue_notifications
        def fail(*args,**kwargs):
            real_notify(*args,**kwargs);db.session.flush()
            raise SQLAlchemyError('private SQL parameters must not be logged')
        with patch('app.queue_notifications',side_effect=fail),self.assertLogs(app.logger,level='ERROR') as logs:
            response=self.prepare(permit_id)
        self.assertEqual(response.status_code,500)
        self.assertIn(b'No changes or notifications were saved',response.data)
        self.assertNotIn('private SQL parameters',' '.join(logs.output))
        with app.app_context():
            self.assertEqual(db.session.get(PermitApplication,permit_id).status,'Approved')
            self.assertEqual(self.notices(permit_id),[])
            self.assertEqual(ActivityLog.query.filter_by(action='PERMIT_READY_FOR_RELEASE').count(),0)
        original=sqlite3.OperationalError('database is locked');original.sqlite_errorcode=sqlite3.SQLITE_BUSY
        with patch('app.update_status',side_effect=OperationalError('SQL',{},original)),self.assertLogs(app.logger,level='ERROR'):
            response=self.prepare(permit_id)
        self.assertEqual(response.status_code,409)
        self.assertIn(b'database is busy',response.data)
        self.assertEqual(self.prepare(permit_id).status_code,200)

    def test_simultaneous_ready_updates_create_one_notification_set(self):
        permit_id=self.permit()
        barrier=threading.Barrier(2);validate=application.validate_transition
        def synchronized(*args):
            result=validate(*args);barrier.wait(timeout=10);return result
        def submit(_):
            with app.test_client() as client:
                with client.session_transaction() as session:
                    session['user_id']=self.ids['admin'];session['csrf_token']='parallel'
                return client.post(f'/permits/{permit_id}',data=dict(status='Ready for Pickup',expected_status='Approved',csrf_token='parallel')).status_code
        with patch('app.validate_transition',side_effect=synchronized),ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(submit,range(2)))
        self.assertEqual(sorted(results),[200,409])
        with app.app_context():
            self.assertEqual(db.session.get(PermitApplication,permit_id).status,'Ready for Pickup')
            self.assertEqual(len(self.notices(permit_id)),3)
            self.assertEqual(ActivityLog.query.filter_by(action='PERMIT_READY_FOR_RELEASE').count(),1)


if __name__=='__main__':unittest.main()
