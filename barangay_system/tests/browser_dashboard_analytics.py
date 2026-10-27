"""Focused Edge check using isolated fixtures, never the configured live database."""
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from test_dashboard_analytics import DashboardAnalyticsTests
from app import app
from models import db, EventRequest
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server


def main():
    fixture=baseline.SystemAcceptanceTests();fixture.setUpClass();fixture.setUp()
    writer=DashboardAnalyticsTests();writer.ids=fixture.ids
    with app.app_context():
        for index,status in enumerate(['Pending','Under Review','Endorsed to Admin','Approved','Rejected','Cancelled','Completed']):
            writer.record('events',status,datetime.now(UTC).replace(tzinfo=None)-timedelta(days=index*25))
        for index,status in enumerate(['Pending','Approved','Rejected','Ready for Pickup']):
            writer.record('permits',status,datetime.now(UTC).replace(tzinfo=None)-timedelta(days=index*25))
        for status in ['Filed','For Hearing / Scheduled','Resolved','Closed']:
            writer.record('blotters',status)
        db.session.commit()
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    output=Path('audit-artifacts');output.mkdir(exist_ok=True)
    errors=[];checks=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.clock.install()
            base=f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/login');page.locator('[name=username]').fill('adminx');page.locator('[name=password]').fill('StrongPass123')
            page.locator('button[type=submit]').click();page.wait_for_url('**/dashboard')
            root=page.locator('#barangay-analytics')
            expect(root.locator('canvas')).to_have_count(8)
            expect(root.locator('.analytics-overview-card').first.locator('strong')).to_have_text('7')
            for width in [1440,1024,768,390]:
                page.set_viewport_size({'width':width,'height':1000});page.clock.fast_forward(250)
                if page.evaluate('document.documentElement.scrollWidth > innerWidth + 2'):
                    page.screenshot(path=str(output/'analytics-overflow.png'),full_page=True)
                    overflowing=page.evaluate("[...document.querySelectorAll('body *')].filter(n => n.getBoundingClientRect().right > innerWidth + 2 && n.getBoundingClientRect().width).slice(0,16).map(n => [n.tagName,n.className,n.getBoundingClientRect().width,n.getBoundingClientRect().right])")
                    raise AssertionError((width,overflowing))
                assert root.locator('canvas').evaluate_all('nodes => nodes.every(n => n.width > 0 && n.height > 0)')
                checks.append({'width':width,'overflow':False,'charts':8})
                if width in [1440,390]:root.screenshot(path=str(output/f'analytics-{width}.png'))
            page.set_viewport_size({'width':1440,'height':1000});page.clock.fast_forward(250)
            root.locator('#data-events-status summary').click()
            with app.app_context():
                writer.record('events','Approved');db.session.commit()
            # Advance the real polling interval; no refresh-button click or page navigation.
            page.clock.fast_forward(31000)
            expect(root.locator('.analytics-overview-card').first.locator('strong')).to_have_text('8')
            expect(root.locator('#data-events-status')).to_have_attribute('open','')
            with app.app_context():
                event=EventRequest.query.filter_by(status='Approved').first();event.status='Rejected';db.session.commit()
            root.locator('[data-analytics-refresh]').click()
            expect(root.locator('#analytics-events .analytics-metric').nth(3).locator('strong')).to_have_text('2')
            with app.app_context():EventRequest.query.delete();db.session.commit()
            root.locator('[data-analytics-refresh]').click()
            expect(root.locator('.analytics-overview-card').first.locator('strong')).to_have_text('0')
            expect(root.locator('#analytics-events canvas')).to_have_count(0)
            expect(root.locator('#analytics-events')).to_contain_text('No event requests yet')
            # A failed refresh retains the last successful state and allows retry.
            page.route('**/dashboard/analytics',lambda route:route.fulfill(status=500,body='Unavailable'))
            root.locator('[data-analytics-refresh]').click()
            expect(root.locator('[data-analytics-state]')).to_contain_text('Showing the last loaded data')
            expect(root.locator('[data-analytics-refresh]')).to_be_enabled()
            expect(root.locator('.analytics-overview-card').first.locator('strong')).to_have_text('0')
            page.unroute('**/dashboard/analytics')
            # Existing report Canvas rendering still works with the shared renderer.
            page.goto(base+'/reports?report_type=permits')
            assert page.locator('canvas.report-chart').count()>0
            browser.close()
        assert not errors,errors
        result={'viewports':checks,'javascript_errors':errors,'automatic_refresh':'passed',
                'status_update_and_deletion':'passed','expanded_table_preserved':'passed',
                'refresh_failure_recovery':'passed','existing_report_charts':'passed'}
        (output/'dashboard-analytics-browser.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result))
    finally:
        server.shutdown();fixture.tearDownClass()


if __name__=='__main__':main()
