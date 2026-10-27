"""Optional real-browser audit. Run from barangay_system; only isolated test data."""
import json
import base64
from io import BytesIO
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from app import app
from models import db, User
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright


def main():
    fixture=baseline.SystemAcceptanceTests()
    fixture.setUpClass();fixture.setUp()
    fixture.clear_auth()
    registration = fixture.registration_data('browserresident', 'browserresident@test.local')
    registration['valid_id_file'] = (BytesIO(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
    )), 'identity.png')
    assert fixture.post('/register', registration).status_code == 302
    with app.app_context():
        review_id = User.query.filter_by(username='browserresident').one().id
    fixture.create_permit(status='Pending')
    fixture.create_permit(status='Approved')
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    output=Path('audit-artifacts');output.mkdir(exist_ok=True)
    results=[];errors=[];overflows=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
            for role,username in [('admin','adminx'),('staff','staffx'),('resident','resx')]:
                context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
                page=context.new_page()
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base+'/login');page.locator('[name=username]').fill(username);page.locator('[name=password]').fill('StrongPass123')
                page.locator('button[type=submit]').click();page.wait_for_url('**/dashboard')
                paths=['/dashboard','/permits','/events','/blotters','/schedules','/notifications','/payments','/announcements','/profile']
                if role=='admin':paths+=['/users','/configuration','/reports?report_type=permits','/activity-logs','/front-desk']
                if role=='staff':paths+=['/users','/front-desk']
                for width in [1440,1024,768,390]:
                    page.set_viewport_size({'width':width,'height':900})
                    for path in paths:
                        started=time.perf_counter();response=page.goto(base+path);page.wait_for_load_state('networkidle')
                        elapsed=round((time.perf_counter()-started)*1000,1)
                        assert response.status==200,(role,path,response.status)
                        overflow=page.evaluate('document.documentElement.scrollWidth > innerWidth + 2')
                        if overflow:overflows.append({'role':role,'path':path,'width':width})
                        results.append({'role':role,'path':path,'width':width,'status':response.status,'navigation_ms':elapsed})
                        if role=='admin' and path=='/dashboard' and width in [1440,390]:page.screenshot(path=str(output/f'dashboard-{width}.png'),full_page=True)
                    if width==390:
                        page.locator('#menuButton').click()
                        assert page.locator('body').evaluate("e=>e.classList.contains('drawer-open')")
                        page.keyboard.press('Escape')
                        assert not page.locator('body').evaluate("e=>e.classList.contains('drawer-open')")
                if role=='admin':
                    page.set_viewport_size({'width':1440,'height':900})
                    downloads=[]
                    page.on('download',lambda download:downloads.append(download.suggested_filename))
                    page.goto(base+f'/users/{review_id}')
                    page.wait_for_load_state('networkidle')
                    assert page.locator('img.id-preview').evaluate('image => image.complete && image.naturalWidth > 0')
                    assert not downloads,downloads
                    for name in ['Verify ID','Verify Address','Approve Account']:
                        page.get_by_role('button',name=name,exact=True).click()
                        page.wait_for_load_state('networkidle')
                        assert page.get_by_role('button',name=name,exact=True).count()==0
                    assert page.get_by_role('button',name='Reject Account',exact=True).count()==0
                    page.goto(base+'/reports?report_type=permits')
                    assert page.locator('canvas.report-chart').count()>0
                    with page.expect_download() as event:page.get_by_role('button',name='Download PDF Report').click()
                    download=event.value
                    assert download.failure() is None
                    download.save_as(output/'browser-permit-report.pdf')
                    page.wait_for_timeout(15500)
                    assert page.get_by_role('button',name='Download PDF Report').count()==1
                context.close()
            browser.close()
        (output/'browser-results.json').write_text(json.dumps({'pages':results,'javascript_errors':errors,'overflow':overflows},indent=2),encoding='utf-8')
        print(json.dumps({'page_checks':len(results),'javascript_errors':errors,'overflow':overflows,
                          'id_image_rendered':True,'automatic_id_downloads':0,'account_approval_ui': 'passed'}))
        assert not errors,errors
        assert not overflows,overflows
    finally:
        server.shutdown()
        fixture.tearDownClass()


if __name__=='__main__':main()
