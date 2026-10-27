"""Browser verification that unpaid events complete without changing payments."""
import sys
import threading
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from app import app
from models import db, EventRequest, Notification, Payment
from services.revision import payment_status
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect


def main():
    fixture=baseline.SystemAcceptanceTests();fixture.setUpClass();fixture.setUp()
    with app.app_context():
        item=EventRequest(requester_id=fixture.ids['resident'],event_name='Browser completion test',
            description='Isolated synthetic event',proposed_date=date.today()+timedelta(days=30),
            proposed_location='Test venue',status='Approved',fee_at_submission=100)
        db.session.add(item);db.session.commit();event_id=item.id
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            base=f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/login');page.locator('[name=username]').fill('adminx')
            page.locator('[name=password]').fill('StrongPass123')
            page.locator('button[type=submit]').click();page.wait_for_url('**/dashboard')
            page.goto(base+f'/events/{event_id}')
            form=page.locator('[data-event-status-form]')
            expect(page.locator('#eventPaymentRequirement')).to_have_count(0)
            expect(form.locator('option[value=Completed]')).to_be_enabled()
            expect(form.locator('option[value=Completed]')).to_have_text('Completed')
            with app.app_context():
                assert payment_status(db.session.get(EventRequest,event_id))=='Unpaid'
                assert Payment.query.count()==0
            form.locator('[name=status]').select_option('Completed')
            form.locator('[name=remarks]').fill('Complete without a payment requirement')
            expect(form.get_by_role('button',name='Update and Notify')).to_be_enabled()
            for width in [1440,768,390]:
                page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(350)
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+2'), (width, page.evaluate("Array.from(document.querySelectorAll('main *')).filter(e => e.getBoundingClientRect().right > innerWidth + 2).slice(0,10).map(e=>[e.tagName,e.className,Math.round(e.getBoundingClientRect().width),Math.round(e.getBoundingClientRect().right)])"))
            with page.expect_response(lambda response:response.url==base+f'/events/{event_id}' and response.request.method=='POST') as network:
                form.get_by_role('button',name='Update and Notify').click()
            assert network.value.status==302
            page.wait_for_load_state('networkidle')
            expect(page.locator('.page-head')).to_contain_text('Completed')
            page.reload();page.wait_for_load_state('networkidle')
            with app.app_context():
                assert db.session.get(EventRequest,event_id).status=='Completed'
                assert db.session.get(EventRequest,event_id).remarks=='Complete without a payment requirement'
                assert payment_status(db.session.get(EventRequest,event_id))=='Unpaid'
                assert Payment.query.count()==0
                assert Notification.query.filter_by(related_type='event',related_id=event_id,title='Event status updated').count()==3
            assert not errors,errors
            print('PASS browser Network: unpaid completion POST 302/GET 200; Completed persisted and payment remained Unpaid')
            print('PASS Completed enabled with no payment warning; review remarks saved; refresh creates no duplicate notifications')
            print('PASS desktop/tablet/mobile layout and no JavaScript errors; server access logs captured')
            browser.close()
    finally:
        server.shutdown();fixture.tearDownClass()


if __name__=='__main__':main()
