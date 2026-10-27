"""Headless assisted registration and four-service UI check on isolated SQLite."""
import sys
import threading
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from app import app
from models import db, User, PermitApplication, EventRequest, BlotterCase, Schedule, QueueEntry
from services.revision import LOCAL_TIMEZONE
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect


def main():
    fixture = baseline.SystemAcceptanceTests()
    fixture.setUpClass(); fixture.setUp()
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
            page = browser.new_page(viewport={'width':1440, 'height':1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            base = f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/login')
            page.locator('[name=username]').fill('staffx'); page.locator('[name=password]').fill('StrongPass123')
            page.locator('button[type=submit]').click(); page.wait_for_url('**/dashboard')
            page.goto(base+'/front-desk')
            page.get_by_role('link', name='New Walk-in Resident', exact=True).click()
            page.locator('[name=full_name]').fill('Paper Form Resident')
            page.locator('[name=address]').fill('25 Paper Street')
            page.locator('[name=purok_zone]').fill('Purok 2')
            page.locator('[name=is_family_head]').select_option('No')
            page.locator('[name=has_disability]').select_option('No')
            page.locator('[name=privacy_consent]').check()
            page.get_by_role('button', name='Save Resident and Continue to Service').click()
            page.wait_for_url('**/front-desk?resident_id=*')
            with app.app_context():
                resident_id = User.query.filter_by(full_name='Paper Form Resident').one().id
            front = base+f'/front-desk?resident_id={resident_id}'
            form = page.locator('#assistedRequestForm')
            for kind in ['permit','event','blotter','schedule']:
                page.goto(front)
                page.locator('#walkinService').select_option(kind)
                panel = page.locator(f'[data-walkin-kind="{kind}"]')
                expect(panel).to_be_visible()
                if kind == 'permit':
                    form.locator('[name=permit_type_id]').select_option(str(fixture.ids['permit_type']))
                    panel.locator('[name=purpose]').fill('Paper form purpose')
                elif kind == 'event':
                    for key,value in fixture.future_event_data().items():
                        field = panel.locator(f'[name="{key}"]')
                        if field.count():
                            if field.evaluate('el=>el.tagName') == 'SELECT': field.select_option(str(value))
                            else: field.fill(str(value))
                elif kind == 'blotter':
                    for key,value in {'respondent_name':'Confidential Respondent', 'incident_type':'Complaint',
                        'incident_date':'2026-01-01T10:00', 'incident_location':'Confidential Location',
                        'narrative':'Confidential Narrative'}.items(): panel.locator(f'[name={key}]').fill(value)
                else:
                    expect(page.locator('#walkinRelated option')).to_have_count(4)
                    with app.app_context(): permit_id = PermitApplication.query.one().id
                    page.locator('#walkinRelated').select_option(f'permit:{permit_id}')
                    panel.locator('[name=scheduled_datetime]').fill((datetime.now(LOCAL_TIMEZONE)+timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'))
                    panel.locator('[name=purpose]').fill('Manual follow-up')
                assert form.evaluate('el=>el.checkValidity()'), kind
                for width in [1440,768,390]:
                    page.set_viewport_size({'width':width,'height':1000}); page.wait_for_timeout(180)
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth+2'), (kind,width)
                form.get_by_role('button', name='Create Walk-in Request and Queue Number').click()
                page.wait_for_url('**/acknowledgment')
                expect(page.get_by_role('heading', name='Walk-in Transaction Acknowledgment')).to_be_visible()
                if kind == 'blotter': assert 'Confidential' not in page.locator('main').inner_text()
                page.emulate_media(media='print')
                assert not page.locator('.sidebar').is_visible()
                page.emulate_media(media='screen')
                print(f'PASS {kind}: persisted request, printable slip, desktop/tablet/mobile layout')
            page.goto(front)
            page.get_by_role('button', name='View References / Manual Follow-up').click()
            expect(page.locator('#walkinFollowup p')).to_have_count(4)
            expect(page.locator('#walkinFollowup a')).to_have_count(4)
            with app.app_context():
                for model in [PermitApplication,EventRequest,BlotterCase,Schedule]: assert model.query.count()==1
                assert QueueEntry.query.count()==4
                queue_id = QueueEntry.query.order_by(QueueEntry.id).first().id
            for action in ['call','serve','complete']:
                with page.expect_navigation():
                    page.locator(f'form[action="/front-desk/queue/{queue_id}/{action}"] button').click()
            with app.app_context():
                assert db.session.get(QueueEntry,queue_id).status=='Completed'
                assert PermitApplication.query.one().status=='Pending'
            assert not errors,errors
            print('PASS manual follow-up, queue completion without approval, no JavaScript errors')
            browser.close()
    finally:
        server.shutdown(); fixture.tearDownClass()


if __name__ == '__main__': main()
