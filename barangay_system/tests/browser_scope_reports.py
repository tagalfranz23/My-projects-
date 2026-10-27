import sys, threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_system as baseline
import test_dashboard_analytics as dashboard
from app import app
from models import db
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
fixture=baseline.SystemAcceptanceTests();fixture.setUpClass();fixture.setUp()
writer=dashboard.DashboardAnalyticsTests();writer.ids=fixture.ids
with app.app_context():
    for kind,status in [('permits','Pending'),('events','Approved'),('blotters','Filed')]:writer.record(kind,status)
    db.session.commit()
server=make_server('127.0.0.1',0,app,threaded=True)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
        page=browser.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        base=f'http://127.0.0.1:{server.server_port}'
        page.goto(base+'/login')
        page.locator('[name=username]').fill('adminx');page.locator('[name=password]').fill('StrongPass123')
        page.locator('button[type=submit]').click();page.wait_for_url('**/dashboard');page.goto(base+'/reports')
        assert page.locator('h3',has_text='Population Statistics').count()==1
        assert page.locator('h3',has_text='Gender Distribution').count()==1
        assert page.locator('canvas[data-chart]').evaluate_all("nodes => nodes.some(n => JSON.parse(n.dataset.chart).title === 'Gender Distribution')")
        print('PASS population table and gender chart rendered')
        assert page.locator('main select, main input:not([type=hidden]), main textarea').count()==0
        for width in [1440,768,390]:
            page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(300)
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 2'),width
            print(f'PASS reports width {width}: no overflow, no filters')
        with page.expect_download() as download:page.get_by_role('button',name='Download PDF Report',exact=True).click()
        assert download.value.suggested_filename.endswith('.pdf')
        assert not errors,errors
        print('PASS PDF download; no JavaScript errors');browser.close()
finally:
    server.shutdown();fixture.tearDownClass()
