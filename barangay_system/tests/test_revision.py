"""Revision acceptance, security and real-data regression tests (isolated SQLite)."""
import unittest
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import test_system as baseline
from app import app, parse_money
from models import (db, utcnow, Payment, PaymentAuthority, PermitApplication, PermitType,
    ServiceRequirement, ApplicationRequirement, PermitSignatory, Announcement,
    ActivityLog, QueueEntry, User, EventRequest, EventReservation)
from services.analytics import build_report, validate_filters, REPORT_TYPES
from services.revision import local_datetime, payment_status, active_announcements


class RevisionTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    post = baseline.SystemAcceptanceTests.post
    create_permit = baseline.SystemAcceptanceTests.create_permit
    future_event_data = baseline.SystemAcceptanceTests.future_event_data
    docx_xml = staticmethod(baseline.SystemAcceptanceTests.docx_xml)
    setUpClass = classmethod(lambda cls: app.config.update(TESTING=True))


    def test_payment_authority_snapshot_duplicate_and_receipt(self):
        pid,_=self.create_permit(status='Approved',fee_at_submission=Decimal('50'))
        payload={'kind':'permit','request_id':pid,'amount':'50','status':'Paid','method':'Cash'}
        for role in ['resident','staff']:
            self.auth(role)
            self.assertEqual(self.post('/payments',payload).status_code,403)
        self.auth('admin')
        self.assertEqual(self.post('/payments/authority',{'user_id':self.ids['staff'],'grant':'1'}).status_code,302)
        with app.app_context():
            db.session.get(PermitType,self.ids['permit_type']).fee=Decimal('60')
            db.session.commit()
        self.auth('staff')
        self.assertEqual(self.post('/payments',dict(payload,amount='60')).status_code,400)
        self.assertEqual(self.post('/payments',payload).status_code,302)
        self.assertEqual(self.post('/payments',payload).status_code,409)
        with app.app_context():
            payment=Payment.query.one()
            self.assertEqual(payment.amount_paid,Decimal('50'))
            self.assertEqual(payment.fee_snapshot,Decimal('50'))
            payment_id=payment.id
        self.auth('resident')
        receipt=self.client.get(f'/payments/{payment_id}/receipt.docx')
        self.assertEqual(receipt.status_code,200)
        self.assertIn('PAYMENT RECEIPT',self.docx_xml(receipt))
        self.auth('other')
        self.assertEqual(self.client.get(f'/payments/{payment_id}/receipt.docx').status_code,403)

    def test_waiver_free_service_and_optional_signatory_authorization(self):
        pid,_=self.create_permit(status='Approved',fee_at_submission=50)
        self.auth('admin')
        self.assertEqual(self.post(f'/permits/{pid}/sign').status_code,403)
        self.assertEqual(self.post('/payments',{'kind':'permit','request_id':pid,'status':'Waived','amount':'0','notes':'Approved exemption'}).status_code,302)
        with app.app_context():
            payment=Payment.query.one()
            self.assertEqual(payment_status(db.session.get(PermitApplication,pid)),'Waived')
            payment_id=payment.id
        self.assertEqual(self.client.get(f'/payments/{payment_id}/receipt.docx').status_code,409)
        free,_=self.create_permit(status='Approved',fee=0)
        self.assertEqual(self.post('/payments',{'kind':'permit','request_id':free,'amount':'0'}).status_code,409)
        with app.app_context():
            self.assertEqual(payment_status(db.session.get(PermitApplication,free)),'Not Required')

    def test_walkin_acknowledgment_print_ownership_and_atomic_requirements(self):
        self.auth('staff')
        with app.app_context():
            requirement=ServiceRequirement(permit_type_id=self.ids['permit_type'],name='Proof',requirement_type='Document',is_required=True)
            db.session.add(requirement)
            db.session.commit()
            rid=requirement.id
        payload={'resident_id':self.ids['resident'],'permit_type_id':self.ids['permit_type'],'purpose':'Walk-in test',f'requirement_{rid}_value':'not a file'}
        self.post('/front-desk',payload)
        with app.app_context():
            self.assertEqual(PermitApplication.query.count(),0)
            self.assertEqual(QueueEntry.query.count(),0)
        payload[f'requirement_{rid}_file']=(BytesIO(b'%PDF-1.4\nproof'),'proof.pdf')
        response=self.post('/front-desk',payload)
        self.assertEqual(response.status_code,302)
        page=self.client.get(response.location)
        self.assertIn(b'Walk-in Transaction Acknowledgment',page.data)
        self.assertIn(b'window.print()',page.data)
        with app.app_context():
            item=PermitApplication.query.one()
            self.assertEqual(item.applicant_id,self.ids['resident'])
            self.assertEqual(item.encoded_by_staff_id,self.ids['staff'])
            pid=item.id
        self.auth('resident')
        doc=self.client.get(f'/front-desk/permits/{pid}/claim-stub.docx')
        self.assertEqual(doc.status_code,200)
        self.assertIn('WALK-IN TRANSACTION ACKNOWLEDGMENT',self.docx_xml(doc))
        self.auth('other')
        self.assertEqual(self.client.get(response.location).status_code,403)

    def test_announcement_boundaries_archive_and_timezone(self):
        self.assertEqual(local_datetime('2026-10-05T08:00').isoformat(),'2026-10-05T00:00:00')
        with app.app_context():
            now=utcnow()
            for name,start,end in [('Future',now+timedelta(days=1),now+timedelta(days=2)),('Current',now-timedelta(days=1),now+timedelta(days=1)),('Expired',now-timedelta(days=2),now-timedelta(seconds=1))]:
                db.session.add(Announcement(title=name,body='Announcement',created_by=self.ids['admin'],approved_by=self.ids['admin'],status='Scheduled' if start>now else 'Published',is_published=True,publish_start_at=start,expires_at=end))
            db.session.commit()
        self.auth('resident')
        response=self.client.get('/announcements')
        self.assertIn(b'Current',response.data)
        self.assertNotIn(b'>Future',response.data)
        self.assertNotIn(b'>Expired',response.data)
        with app.app_context():
            self.assertEqual(Announcement.query.filter_by(title='Expired').one().status,'Expired')
            self.assertEqual(active_announcements().count(),1)
            expired=Announcement.query.filter_by(title='Expired').one().id
            self.assertEqual(ActivityLog.query.filter_by(action='ANNOUNCEMENT_EXPIRED').count(),1)
        self.client.get('/announcements')
        self.auth('admin')
        self.assertEqual(self.post(f'/announcements/{expired}/delete').status_code,302)
        with app.app_context():
            self.assertEqual(Announcement.query.count(),3)
            self.assertEqual(Announcement.query.filter_by(id=expired).one().status,'Archived')

    def test_program_removal_preserves_linked_announcements_and_categories(self):
        from models import CommunityProgram
        from services.revision import CATEGORIES
        with app.app_context():
            moment = utcnow()
            program = CommunityProgram(program_type='Scholarship', title='Historical program',
                description='Preserved record', eligibility='Historical criteria',
                application_start=moment-timedelta(days=1), application_deadline=moment+timedelta(days=1),
                created_by=self.ids['admin'])
            db.session.add(program)
            db.session.flush()
            notice = Announcement(title='Historical linked notice', body='Preserved announcement details',
                category='Scholarship', program_id=program.id, status='Published', is_published=True,
                publish_start_at=moment-timedelta(days=1), expires_at=moment+timedelta(days=1),
                created_by=self.ids['admin'])
            db.session.add(notice)
            db.session.commit()
            program_id, notice_id = program.id, notice.id
        self.assertFalse(any(rule.rule.startswith(('/programs', '/program-applications'))
                             for rule in app.url_map.iter_rules()))
        for role in ['admin', 'staff', 'resident']:
            self.auth(role)
            page = self.client.get('/announcements')
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'Preserved announcement details', page.data)
            self.assertIn(b'Scholarship', page.data)  # Existing category stays visible.
            for removed in [b'Scholarship &amp; Ayuda Programs', b'View Program', b'href="/programs']:
                self.assertNotIn(removed, page.data)
            self.assertEqual(self.client.get('/programs').status_code, 404)
            self.assertEqual(self.post('/programs', {}).status_code, 404)
            self.assertEqual(self.client.get('/program-applications/1').status_code, 404)
        self.auth('staff')
        self.assertEqual(self.post('/announcements', {
            'title': 'Draft notice', 'body': 'New announcement details', 'category': 'General Advisory',
            'publish_start_at': (moment+timedelta(hours=7)).strftime('%Y-%m-%dT%H:%M'),
            'expires_at': (moment+timedelta(days=1,hours=8)).strftime('%Y-%m-%dT%H:%M'),
        }).status_code, 302)
        with app.app_context():
            draft = Announcement.query.filter_by(title='Draft notice').one()
            self.assertEqual(draft.status, 'Draft')
            draft_id = draft.id
        self.auth('admin')
        self.assertEqual(self.post(f'/announcements/{draft_id}/approve', {}).status_code, 302)
        page = self.client.get('/announcements')
        for category in CATEGORIES:
            self.assertIn(category.encode(), page.data)
        self.assertIn(b'New announcement details', page.data)
        self.assertEqual(self.post(f'/announcements/{notice_id}/delete', {}).status_code, 302)
        with app.app_context():
            self.assertEqual(db.session.get(Announcement, notice_id).status, 'Archived')
            self.assertIsNotNone(db.session.get(CommunityProgram, program_id))



    def test_report_filters_totals_docx_and_privacy(self):
        self.create_permit(status='Pending',fee_at_submission=50)
        paid,_=self.create_permit(status='Approved',fee=60)
        self.auth('admin')
        self.post('/payments',{'kind':'permit','request_id':paid,'amount':'60'})
        with app.app_context():
            report=build_report(validate_filters({'report_type':'payments'}))
            self.assertEqual(dict(report['sections'][0]['rows'])['Collected (PHP)'],60)
            for kind in REPORT_TYPES:
                self.assertTrue(build_report(validate_filters({'report_type':kind}))['sections'])
        self.assertEqual(self.client.get('/reports?report_type=payments&service=1').status_code,200)
        self.assertEqual(self.client.get('/reports?start=2026-12-01&end=2026-01-01').status_code,200)
        response=self.post('/reports/export/pdf',{'report_type':'permits','status':'Pending'})
        self.assertEqual(response.status_code,200)
        xml=baseline.SystemAcceptanceTests.pdf_text(response)
        self.assertNotIn('status=Pending',xml)
        self.assertIn('Overall Barangay Report',xml)
        self.assertIn('Category / Metric',xml)
        self.assertNotIn('password_hash',xml)
        for role in ['resident','staff']:
            self.auth(role)
            self.assertEqual(self.client.get('/reports').status_code,403)
            self.assertEqual(self.post('/reports/export/pdf').status_code,403)
            self.assertEqual(self.client.get('/reports/chart/0.png').status_code,403)

    def test_new_pages_all_roles_and_csrf(self):
        for role in ['resident','staff','admin']:
            self.auth(role)
            for url in ['/dashboard','/payments','/announcements']:
                self.assertEqual(self.client.get(url).status_code,200,(role,url))
        self.assertEqual(self.client.post('/payments',data={'amount':'1'}).status_code,400)
        for value in ['NaN','Infinity','-1','100000000']:
            with self.assertRaises(ValueError):
                parse_money(value)

    def test_all_static_template_endpoints_exist(self):
        import re
        for path in Path(app.template_folder if Path(app.template_folder).is_absolute() else Path(app.root_path)/app.template_folder).glob('*.html'):
            for endpoint in re.findall(r"url_for\(['\"]([^'\"]+)",path.read_text(encoding='utf-8')):
                self.assertIn(endpoint,app.view_functions,(path.name,endpoint))

    def test_full_permit_review_payment_signature_release_feedback(self):
        from PIL import Image
        with app.app_context():
            requirement=ServiceRequirement(permit_type_id=self.ids['permit_type'],name='Reason',requirement_type='Text',is_required=True)
            optional=ServiceRequirement(permit_type_id=self.ids['permit_type'],name='Optional',requirement_type='Text',is_required=False)
            db.session.add_all([requirement,optional]);db.session.commit();rid=requirement.id
            signature=Path(app.config['SIGNATURE_FOLDER'])/'test-real-signature.png'
            Image.new('RGB',(60,20),'navy').save(signature)
            db.session.add(PermitSignatory(permit_type_id=self.ids['permit_type'],authorized_admin_id=self.ids['admin'],name='Test Official',title='Authorized Official',file_path=signature.name))
            db.session.commit()
        self.auth('resident')
        self.post('/permits',{'permit_type_id':self.ids['permit_type'],'purpose':'Complete workflow',f'requirement_{rid}_value':'Evidence'})
        with app.app_context():
            item=PermitApplication.query.one();pid=item.id
            req=ApplicationRequirement.query.filter_by(application_id=pid,is_required=True).one();snapshot=req.id
        self.auth('staff')
        self.assertEqual(self.post(f'/permits/{pid}/requirements/{snapshot}/review',{'status':'Needs Resubmission','note':'Please correct evidence'}).status_code,302)
        self.assertEqual(self.post(f'/permits/{pid}/requirements/{snapshot}/review',{'status':'Verified'}).status_code,409)
        self.auth('resident')
        self.assertEqual(self.post(f'/permits/{pid}/requirements/{snapshot}/submit',{'value':'Corrected evidence'}).status_code,302)
        self.auth('staff')
        self.assertEqual(self.post(f'/permits/{pid}/requirements/{snapshot}/review',{'status':'Verified'}).status_code,302)
        self.assertEqual(self.post(f'/permits/{pid}',{'status':'Endorsed to Admin'}).status_code,200)
        self.assertEqual(self.post(f'/permits/{pid}',{'status':'Approved'}).status_code,400)
        self.auth('admin')
        self.assertEqual(self.post(f'/permits/{pid}',{'status':'Approved'}).status_code,200)
        self.assertEqual(self.post('/payments',{'kind':'permit','request_id':pid,'amount':'50'}).status_code,302)
        self.assertEqual(self.post(f'/permits/{pid}/sign').status_code,302)
        self.auth('staff')
        self.assertEqual(self.post(f'/front-desk/permits/{pid}/release',{'identity_confirmed':'1'}).status_code,302)
        self.assertEqual(self.post(f'/front-desk/permits/{pid}/release',{'identity_confirmed':'1'}).status_code,400)
        self.auth('resident')
        self.assertEqual(self.client.get(f'/permits/{pid}/approved-document.docx').status_code,200)
        self.assertEqual(self.post(f'/permits/{pid}/feedback',{'rating':'5','comment':'Completed'}).status_code,302)


    def test_concurrent_payment_is_unique(self):
        pid,_=self.create_permit(status='Approved')
        def submit(path,role,payload):
            with app.test_client() as client:
                with client.session_transaction() as session:
                    session['user_id']=self.ids[role];session['csrf_token']='concurrent-token'
                return client.post(path,data=dict(payload,csrf_token='concurrent-token')).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:submit('/payments','admin',{'kind':'permit','request_id':pid,'amount':'50'}),range(2)))
        self.assertEqual(sorted(results),[302,409])
        with app.app_context():
            self.assertEqual(Payment.query.count(),1)

    def test_concurrent_event_and_walkin_queue(self):
        def post_as(role,path,payload):
            with app.test_client() as client:
                with client.session_transaction() as session:
                    session['user_id']=self.ids[role];session['csrf_token']='parallel'
                return client.post(path,data=dict(payload,csrf_token='parallel')).status_code
        data=self.future_event_data(days=80)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda role:post_as(role,'/events',data),['resident','other']))
        self.assertEqual(sorted(results),[200,302])
        with app.app_context():
            self.assertEqual(EventRequest.query.count(),1)
            self.assertEqual(EventReservation.query.filter_by(status='Reserved').count(),1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:post_as('staff','/front-desk',{'resident_id':self.ids['resident'],'permit_type_id':self.ids['permit_type'],'purpose':'Parallel walk-in'}),range(2)))
        self.assertEqual(results,[302,302])
        with app.app_context():
            self.assertEqual(len({q.queue_number for q in QueueEntry.query.all()}),2)

    def test_upload_type_size_path_and_pagination(self):
        self.auth('resident')
        payload={'permit_type_id':self.ids['permit_type'],'purpose':'Upload test'}
        for filename,data in [('bad.exe',b'%PDF-1.4'),('bad.png',b'%PDF-1.4'),('empty.pdf',b'')]:
            self.post('/permits',dict(payload,attachment=(BytesIO(data),filename)))
            with app.app_context():self.assertEqual(PermitApplication.query.count(),0)
        self.assertEqual(self.post('/permits',dict(payload,attachment=(BytesIO(b'%PDF-'+b'x'*(8*1024*1024)), 'big.pdf'))).status_code,413)
        self.post('/permits',dict(payload,attachment=(BytesIO(b'%PDF-1.4\nsafe'), '../../same.pdf')))
        self.post('/permits',dict(payload,attachment=(BytesIO(b'%PDF-1.4\nsafe'), '../../same.pdf')))
        with app.app_context():
            paths=[r.attachment_path for r in PermitApplication.query.all()]
            self.assertEqual(len(set(paths)),2)
            self.assertTrue(all('..' not in p and '/' not in p for p in paths))
            db.session.add_all([PermitApplication(applicant_id=self.ids['resident'],permit_type_id=self.ids['permit_type'],purpose='Page test') for _ in range(35)])
            db.session.commit()
        response=self.client.get('/permits?page=2')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Page 2 of 2',response.data)

    def test_pdf_export_ignores_legacy_report_types_and_preserves_sqlite_integrity(self):
        from pypdf import PdfReader
        from sqlalchemy import text
        self.auth('admin')
        for kind in REPORT_TYPES:
            response=self.post('/reports/export/pdf',{'report_type':kind})
            self.assertEqual(response.status_code,200,kind)
            doc=PdfReader(BytesIO(response.data))
            self.assertTrue(doc.pages,kind)
            self.assertIn('Overall Barangay Report',doc.pages[0].extract_text(),kind)
        with app.app_context():
            self.assertEqual(db.session.execute(text('PRAGMA integrity_check')).scalar(),'ok')
            self.assertEqual(db.session.execute(text('PRAGMA foreign_key_check')).all(),[])

    def test_legacy_announcement_migration_is_additive_and_idempotent(self):
        from migration import upgrade_legacy_schema
        from sqlalchemy import text
        with app.app_context():
            # This is the isolated fixture database, never the configured live database.
            db.session.execute(text('DROP TABLE announcements'))
            db.session.execute(text('CREATE TABLE announcements (id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, is_published BOOLEAN NOT NULL, published_at DATETIME NOT NULL, expires_at DATETIME, created_by INTEGER NOT NULL REFERENCES users(id), created_at DATETIME NOT NULL)'))
            db.session.execute(text("INSERT INTO announcements VALUES (1, 'Legacy preserved', 'Public notice', 1, '2026-01-01 00:00:00', NULL, :user, '2026-01-01 00:00:00')"),{'user':self.ids['admin']})
            db.session.commit()
            upgrade_legacy_schema();upgrade_legacy_schema()
            item=db.session.get(Announcement,1)
            self.assertEqual(item.title,'Legacy preserved')
            self.assertEqual(item.status,'Published')
            self.assertIsNone(item.expires_at)
            self.assertEqual(Payment.query.count(),0)
            self.assertEqual(db.session.execute(text('PRAGMA foreign_key_check')).all(),[])

    def test_uploaded_files_rollback_with_incomplete_request(self):
        self.auth('resident')
        with app.app_context():
            first=ServiceRequirement(permit_type_id=self.ids['permit_type'],name='First document',requirement_type='Document',is_required=True,position=1)
            second=ServiceRequirement(permit_type_id=self.ids['permit_type'],name='Second document',requirement_type='Document',is_required=True,position=2)
            db.session.add_all([first,second]);db.session.commit();rid=first.id
        folder=Path(app.config['UPLOAD_FOLDER']);before=set(folder.iterdir())
        self.post('/permits',{'permit_type_id':self.ids['permit_type'],'purpose':'Rollback',f'requirement_{rid}_file':(BytesIO(b'%PDF-1.4\nrollback'),'test.pdf')})
        self.assertEqual(set(folder.iterdir()),before)
        with app.app_context():self.assertEqual(PermitApplication.query.count(),0)
