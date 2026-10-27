"""Real SQLite aggregation and access tests for the dashboard-only analytics."""
import unittest
from datetime import UTC, date, datetime, timedelta

import test_system as baseline
from app import app
from models import db, EventRequest, PermitApplication, PermitType, BlotterCase
from services.analytics import build_dashboard_analytics


class DashboardAnalyticsTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    setUpClass = classmethod(lambda cls: app.config.update(TESTING=True))

    def record(self, kind, status, created=None, **extra):
        fields = {'status':status, 'created_at':created or datetime.now(UTC).replace(tzinfo=None)}
        if kind == 'events':
            item = EventRequest(requester_id=self.ids['resident'], event_name='Fixture event', description='Fixture description',
                                proposed_date=date(2026,12,1), **fields, **extra)
        elif kind == 'permits':
            item = PermitApplication(applicant_id=self.ids['resident'], purpose='Fixture purpose',
                                     permit_type_id=extra.pop('permit_type_id',self.ids['permit_type']), **fields, **extra)
        else:
            item = BlotterCase(complainant_id=self.ids['resident'], respondent_name='Private respondent',
                               incident_date=datetime(2026,1,1), incident_location='Private location',
                               narrative='Private case narrative', incident_type=extra.pop('incident_type','Noise'),
                               **fields, **extra)
        db.session.add(item)
        return item

    def test_all_statuses_categories_and_active_cases_use_recorded_rows(self):
        with app.app_context():
            for status in ['Pending','Under Review','Endorsed to Admin','Approved','Rejected','Cancelled','Completed']:
                self.record('events',status)
            for status in ['Pending','Under Review','Endorsed to Admin','Approved','Ready for Pickup','Completed','Rejected','Cancelled']:
                self.record('permits',status)
            for status in ['Filed','Under Review','For Investigation','For Hearing / Scheduled','Resolved','Closed']:
                self.record('blotters',status)
            second = PermitType(name='Another service', fee=0, fee_is_configured=True)
            db.session.add(second);db.session.flush()
            self.record('permits','Approved',permit_type_id=second.id)
            self.record('blotters','Filed',incident_type='Property concern')
            db.session.commit()
            data={m['key']:m for m in build_dashboard_analytics()['modules']}
            self.assertEqual([data[k]['total'] for k in ('events','permits','blotters')],[7,9,7])
            self.assertEqual([v['value'] for v in data['events']['metrics']],[7,1,1,1,1])
            self.assertEqual([v['value'] for v in data['permits']['metrics']],[9,1,2,1])
            self.assertEqual([v['value'] for v in data['blotters']['metrics']],[7,5,1,1])
            self.assertEqual(dict(data['permits']['charts'][2]['rows'])['Another service'],1)
            self.assertEqual(dict(data['blotters']['charts'][2]['rows']),{'Noise':6,'Property concern':1})
            for module in data.values():self.assertEqual(sum(v for _,v in module['charts'][0]['rows']),module['total'])

    def test_twelve_month_trends_use_manila_creation_time_and_zero_gaps(self):
        with app.app_context():
            for kind, status in [('events','Pending'),('permits','Pending'),('blotters','Filed')]:
                for stamp in [datetime(2025,3,31,15,59),datetime(2025,3,31,16),datetime(2026,2,28,16),datetime(2026,3,31,16)]:
                    self.record(kind,status,stamp)
            db.session.commit()
            result=build_dashboard_analytics(datetime(2026,3,15,tzinfo=UTC))
            self.assertEqual(result['period'],'Apr 2025 – Mar 2026')
            for module in result['modules']:
                rows=module['charts'][1]['rows']
                self.assertEqual(len(rows),12)
                self.assertEqual(rows[0],['Apr 2025',1]);self.assertEqual(rows[-1],['Mar 2026',1])
                self.assertEqual(sum(count for _,count in rows),2)
                self.assertEqual(module['total'],4)

    def test_empty_data_then_create_update_delete_is_reflected_without_cache(self):
        self.auth('admin')
        empty=self.client.get('/dashboard/analytics')
        self.assertEqual(empty.status_code,200)
        self.assertIn(b'No event requests yet',empty.data)
        with app.app_context():
            for m in build_dashboard_analytics()['modules']:
                self.assertEqual(m['total'],0);self.assertFalse(m['charts'][1]['has_data'])
            event=self.record('events','Pending');db.session.commit();identity=event.id
        created=self.client.get('/dashboard/analytics')
        self.assertNotEqual(empty.data,created.data)
        with app.app_context():
            event=db.session.get(EventRequest,identity);event.status='Approved';db.session.commit()
            event_data=build_dashboard_analytics()['modules'][0]
            self.assertEqual([v['value'] for v in event_data['metrics']],[1,0,1,0,0])
        approved=self.client.get('/dashboard/analytics')
        self.assertNotEqual(created.data,approved.data)
        with app.app_context():db.session.delete(db.session.get(EventRequest,identity));db.session.commit()
        self.assertEqual(empty.data,self.client.get('/dashboard/analytics').data)

    def test_fragment_permissions_scope_escaping_and_no_private_details(self):
        self.assertEqual(self.client.get('/dashboard/analytics').status_code,302)
        for role in ['resident','staff']:
            self.auth(role)
            self.assertEqual(self.client.get('/dashboard/analytics').status_code,403)
            self.assertNotIn(b'id="barangay-analytics"',self.client.get('/dashboard').data)
        self.auth('admin')
        with app.app_context():
            self.record('blotters','Filed',incident_type='<script>alert(1)</script>');db.session.commit()
        response=self.client.get('/dashboard/analytics')
        self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response.headers['Cache-Control'])
        for forbidden in [b'Private respondent',b'Private location',b'Private case narrative',b'<script>alert(1)',b'Household',b'Payments',b'Scholarship']:
            self.assertNotIn(forbidden,response.data)
        self.assertIn(b'&lt;script&gt;',response.data)
        page=self.client.get('/dashboard')
        self.assertEqual(page.status_code,200)
        for marker in [b'id="barangay-analytics"',b'Requires Your Attention']:
            self.assertIn(marker,page.data)

    def test_dashboard_navigation_stays_within_research_scope(self):
        for role in ['admin', 'staff', 'resident']:
            with self.subTest(role=role):
                self.auth(role)
                page = self.client.get('/dashboard')
                self.assertEqual(page.status_code, 200)
                for forbidden in [b'Scholarship', b'Ayuda', b'Community Programs',
                                  b'Payment Status', b'Payments', b'href="/programs',
                                  b'href="/payments']:
                    self.assertNotIn(forbidden, page.data)
                for endpoint in ['permits', 'events', 'blotters', 'schedules', 'notifications']:
                    self.assertIn(f'href="/{endpoint}"'.encode(), page.data)


if __name__ == '__main__':unittest.main()
