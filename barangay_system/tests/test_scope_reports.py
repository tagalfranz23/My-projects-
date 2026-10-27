"""Filter-free reports use persisted core service records only."""
import re
import unittest
from io import BytesIO
from pypdf import PdfReader
import test_dashboard_analytics as dashboard_tests
import test_system as baseline
from app import app
from models import db, PermitApplication
from services.analytics import build_scope_reports


class ScopeReportsTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    post = baseline.SystemAcceptanceTests.post
    record = dashboard_tests.DashboardAnalyticsTests.record
    setUpClass = classmethod(lambda cls: app.config.update(TESTING=True))

    def test_automatic_page_and_export_ignore_legacy_filters(self):
        self.auth('admin')
        with app.app_context():
            self.record('permits', 'Pending')
            self.record('permits', 'Approved')
            self.record('events', 'Rejected')
            self.record('blotters', 'Filed')
            db.session.commit()
            report = build_scope_reports()
            self.assertEqual(dict(report['sections'][0]['rows']), {
                'Permit Reports': 2, 'Event Request Reports': 1,
                'Blotter Reports': 1, 'Total core service records': 4})
        page = self.client.get('/reports?report_type=scholarship&status=Approved&year=1900')
        self.assertEqual(page.status_code, 200)
        html = page.data.decode()
        self.assertNotRegex(html, r'<(?:select|textarea)\b')
        for tag in re.findall(r'<input\b[^>]*>', html):
            self.assertIn('name="csrf_token"', tag)
            self.assertIn('type="hidden"', tag)
        for label in ['Permit Reports', 'Event Request Reports', 'Blotter Reports',
                      'Overall Barangay Report', 'Pending', 'Approved', 'Rejected', 'Filed']:
            self.assertIn(label, html)
        for forbidden in ['Scholarship', 'Ayuda', 'Payment', 'Apply filters', 'Report type']:
            self.assertNotIn(forbidden, html)
        response = self.post('/reports/export/pdf', {'report_type': 'payments', 'status': 'Approved'})
        self.assertEqual(response.status_code, 200)
        document = PdfReader(BytesIO(response.data))
        text = ' '.join(page.extract_text() for page in document.pages)
        self.assertTrue(document.is_encrypted)
        permissions = document.trailer['/Encrypt']['/P']
        self.assertTrue(permissions & 4)  # Printing allowed.
        self.assertEqual(permissions & (8 | 16 | 32), 0)  # No editing, copying, annotation.
        self.assertEqual(self.post('/reports/export/docx').status_code, 404)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertIn('.pdf', response.headers['Content-Disposition'])
        self.assertNotIn('Payment', text)
        self.assertNotIn('Filters:', text)
        self.assertIn('Overall Barangay Report', text)
        with app.app_context():
            self.assertEqual(PermitApplication.query.count(), 2)
            chart_index = next(i for i,s in enumerate(build_scope_reports()['sections'])
                               if s['title'] == 'Permit requests by status')
        self.assertEqual(self.client.get(f'/reports/chart/{chart_index}.png?report_type=payments').mimetype, 'image/png')

    def test_empty_database_has_zero_totals_and_access_is_preserved(self):
        with app.app_context():
            self.assertTrue(all(value == 0 for _,value in build_scope_reports()['sections'][0]['rows']))
        for role in ['staff', 'resident']:
            self.auth(role)
            self.assertEqual(self.client.get('/reports').status_code, 403)
        self.auth('admin')
        page = self.client.get('/reports')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'No records available.', page.data)


if __name__ == '__main__':
    unittest.main()
