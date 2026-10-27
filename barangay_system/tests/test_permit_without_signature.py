"""Unsigned permits retain approval, evidence, payment and identity safeguards."""
import unittest
from decimal import Decimal
import test_system as baseline
from app import app
from models import (db, PermitApplication, PermitSignatory, Payment, Notification,
                    ServiceRequirement, ApplicationRequirement, ActivityLog)
from services.revision import payment_status


class PermitWithoutSignatureTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    post = baseline.SystemAcceptanceTests.post
    create_permit = baseline.SystemAcceptanceTests.create_permit
    docx_xml = staticmethod(baseline.SystemAcceptanceTests.docx_xml)
    setUpClass = classmethod(lambda cls:app.config.update(TESTING=True))

    def test_review_approve_pay_prepare_download_release_without_signature(self):
        with app.app_context():
            definition=ServiceRequirement(permit_type_id=self.ids['permit_type'],name='Evidence',
                requirement_type='Text',is_required=True)
            db.session.add(definition);db.session.commit();definition_id=definition.id
        self.auth('resident')
        self.assertEqual(self.post('/permits',dict(permit_type_id=self.ids['permit_type'],purpose='Unsigned workflow',
            **{f'requirement_{definition_id}_value':'Submitted evidence'})).status_code,302)
        with app.app_context():
            item=PermitApplication.query.one();permit_id=item.id
            requirement_id=item.requirement_records[0].id
        self.auth('staff')
        self.assertEqual(self.post(f'/permits/{permit_id}/requirements/{requirement_id}/review',dict(status='Verified')).status_code,302)
        self.assertEqual(self.post(f'/permits/{permit_id}',dict(status='Endorsed to Admin')).status_code,200)
        self.auth('admin')
        self.assertEqual(self.post(f'/permits/{permit_id}',dict(status='Approved')).status_code,200)
        page=self.client.get(f'/permits/{permit_id}')
        self.assertNotIn(b'Apply Authorized Electronic Signature',page.data)
        self.assertNotIn(b'Final signature is available',page.data)
        self.assertIn(b'Ready for Pickup',page.data)
        self.assertEqual(self.post('/payments',dict(kind='permit',request_id=permit_id,amount='50',status='Paid',method='Cash')).status_code,302)
        with app.app_context():
            payment=Payment.query.one()
            payment_before=(payment.id,payment.status,payment.amount_paid,payment.paid_at,payment.recorded_by)
        self.assertEqual(self.post(f'/permits/{permit_id}',dict(status='Ready for Pickup',remarks='Prepared without a signature')).status_code,200)
        with app.app_context():
            item=db.session.get(PermitApplication,permit_id)
            self.assertEqual(item.status,'Ready for Pickup')
            self.assertFalse(item.is_signed)
            self.assertIsNone(item.signed_by);self.assertIsNone(item.signed_at);self.assertIsNone(item.signature_path)
            self.assertEqual(item.remarks,'Prepared without a signature')
            self.assertEqual(item.reviewed_by,self.ids['admin'])
            self.assertEqual(PermitSignatory.query.count(),0)
            self.assertEqual(ActivityLog.query.filter_by(action='PERMIT_READY_FOR_RELEASE').count(),1)
            self.assertEqual(Notification.query.filter_by(related_type='permit',related_id=permit_id,
                message=f'{item.reference_no}: Ready for Pickup').count(),3)
        self.assertEqual(self.post(f'/permits/{permit_id}',dict(status='Ready for Pickup')).status_code,409)
        self.auth('resident')
        document=self.client.get(f'/permits/{permit_id}/approved-document.docx')
        self.assertEqual(document.status_code,200)
        self.assertNotIn('Electronic signature applied',self.docx_xml(document))
        self.assertEqual(self.client.get(f'/acknowledgments/permit/{permit_id}.docx').status_code,200)
        self.auth('staff')
        self.assertEqual(self.post(f'/front-desk/permits/{permit_id}/release').status_code,302)
        with app.app_context():self.assertEqual(db.session.get(PermitApplication,permit_id).status,'Ready for Pickup')
        self.assertEqual(self.post(f'/front-desk/permits/{permit_id}/release',dict(identity_confirmed='1')).status_code,302)
        with app.app_context():
            item=db.session.get(PermitApplication,permit_id)
            self.assertEqual(item.status,'Completed');self.assertFalse(item.is_signed)
            payment=Payment.query.one()
            self.assertEqual(payment_before,(payment.id,payment.status,payment.amount_paid,payment.paid_at,payment.recorded_by))

    def test_authorization_and_verified_requirements_remain_required(self):
        permit_id,_=self.create_permit(status='Approved',fee=Decimal('50'))
        for role,code in [('resident',403),('staff',400)]:
            self.auth(role)
            self.assertEqual(self.post(f'/permits/{permit_id}',dict(status='Ready for Pickup')).status_code,code)
        with app.app_context():
            self.assertEqual(payment_status(db.session.get(PermitApplication,permit_id)),'Unpaid')
            self.assertEqual(Notification.query.count(),0)
            requirement=ServiceRequirement(permit_type_id=self.ids['permit_type'],name='Required evidence',requirement_type='Text',is_required=True)
            db.session.add(requirement);db.session.flush()
            db.session.add(ApplicationRequirement(application_id=permit_id,definition_id=requirement.id,
                name=requirement.name,requirement_type='Text',is_required=True,status='Submitted'))
            db.session.commit()
        self.auth('admin')
        response=self.post(f'/permits/{permit_id}',dict(status='Ready for Pickup'))
        self.assertEqual(response.status_code,409)
        self.assertIn(b'All required evidence',response.data)
        with app.app_context():self.assertEqual(db.session.get(PermitApplication,permit_id).status,'Approved')

    def test_free_waived_and_unpaid_permits_need_no_signature_or_payment_gate(self):
        for fee in [0,50]:
            permit_id,_=self.create_permit(status='Approved',fee=Decimal(fee))
            self.auth('admin')
            if fee:
                self.assertEqual(self.post('/payments',dict(kind='permit',request_id=permit_id,status='Waived',amount='0',notes='Authorized waiver')).status_code,302)
            self.assertEqual(self.post(f'/permits/{permit_id}',dict(status='Ready for Pickup')).status_code,200)
            self.assertEqual(self.client.get(f'/permits/{permit_id}/approved-document.docx').status_code,200)
        permit_id,_=self.create_permit(status='Ready for Pickup',fee=Decimal('50'))
        self.auth('admin')
        self.assertEqual(self.post(f'/front-desk/permits/{permit_id}/release',dict(identity_confirmed='1')).status_code,302)
        self.assertEqual(self.client.get(f'/permits/{permit_id}/approved-document.docx').status_code,200)


if __name__=='__main__':unittest.main()
