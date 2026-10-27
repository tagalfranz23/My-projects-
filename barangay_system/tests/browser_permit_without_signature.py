"""Verify Permit Details and unsigned pickup/release in a real browser."""
import sys
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from app import app
from models import db, PermitApplication, Payment, PermitSignatory
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect


def main():
    fixture=baseline.SystemAcceptanceTests();fixture.setUpClass();fixture.setUp()
    permit_id,_=fixture.create_permit(status='Approved')
    fixture.auth('admin')
    assert fixture.post('/payments',dict(kind='permit',request_id=permit_id,amount='50')).status_code==302
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            base=f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/login');page.locator('[name=username]').fill('adminx')
            page.locator('[name=password]').fill('StrongPass123');page.locator('button[type=submit]').click()
            page.wait_for_url('**/dashboard');page.goto(base+f'/permits/{permit_id}')
            expect(page.get_by_role('button',name='Apply Authorized Electronic Signature')).to_have_count(0)
            expect(page.locator('.kv').filter(has_text='Payment status')).to_have_count(0)
            expect(page.get_by_role('link',name='View payments',exact=True)).to_have_count(0)
            page.locator('[name=status]').select_option('Ready for Pickup')
            page.locator('[name=remarks]').fill('Prepared without electronic signing')
            with page.expect_response(lambda response:response.request.method=='POST' and response.url==base+f'/permits/{permit_id}') as network:
                page.get_by_role('button',name='Update and Notify').click()
            assert network.value.status==200
            expect(page.locator('.page-head')).to_contain_text('Ready for Pickup')
            with page.expect_download() as acknowledgement:
                page.get_by_role('link',name='Download Acknowledgment (.docx)',exact=True).click()
            assert acknowledgement.value.suggested_filename.endswith('.docx')
            with page.expect_download() as approved:
                page.get_by_role('link',name='Download Approved Permit (.docx)',exact=True).click()
            assert approved.value.suggested_filename.endswith('.docx')
            with app.app_context():
                permit=db.session.get(PermitApplication,permit_id)
                assert permit.status=='Ready for Pickup' and not permit.is_signed
                assert permit.signature_path is None and PermitSignatory.query.count()==0
                assert Payment.query.one().status=='Paid'
            page.goto(base+'/front-desk')
            release=page.locator(f'form[action="/front-desk/permits/{permit_id}/release"]')
            release.locator('[name=identity_confirmed]').check()
            release.get_by_role('button',name='Record Release').click();page.wait_for_load_state('networkidle')
            with app.app_context():
                permit=db.session.get(PermitApplication,permit_id)
                assert permit.status=='Completed' and not permit.is_signed
                assert Payment.query.count()==1 and Payment.query.one().status=='Paid'
            assert not errors,errors
            print('PASS no signature button/configuration required; admin Ready for Pickup update; Paid preserved')
            print('PASS acknowledgment and approved-document downloads; identity-confirmed unsigned release; no JavaScript errors')
            browser.close()
    finally:
        server.shutdown();fixture.tearDownClass()


if __name__=='__main__':main()
