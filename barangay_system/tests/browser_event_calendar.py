"""Isolated browser checks for venue/time filtering and calendar-based submission."""
import sys, threading
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from app import app
from models import db, EventRequest
from services.revision import LOCAL_TIMEZONE
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect


def main():
    fixture=baseline.SystemAcceptanceTests();fixture.setUpClass();fixture.setUp();fixture.auth('resident')
    payload=fixture.future_event_data(days=30,name='Confidential other applicant event')
    assert fixture.post('/events',payload).status_code==302
    with app.app_context():
        item=EventRequest.query.one();item.status='Approved';db.session.commit();event_id=item.id
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000});page.clock.install();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            base=f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/login');page.locator('[name=username]').fill('resy');page.locator('[name=password]').fill('StrongPass123')
            page.locator('button[type=submit]').click();page.wait_for_url('**/dashboard');page.goto(base+'/events')
            root=page.locator('[data-event-calendar]')
            expect(root.locator('[data-calendar-state]')).to_contain_text('Availability refreshed')
            current=datetime.now(LOCAL_TIMEZONE).strftime('%B %Y')
            expect(root.locator('[data-calendar-month]')).to_have_text(current)
            root.get_by_role('button',name='Previous month').click()
            expect(root.locator('[data-calendar-month]')).not_to_have_text(current)
            root.get_by_role('button',name='Next month').click()
            expect(root.locator('[data-calendar-month]')).to_have_text(current)
            target=datetime.fromisoformat(payload['proposed_date']).strftime('%B %Y')
            for _ in range(3):
                if root.locator('[data-calendar-month]').inner_text()==target:break
                root.get_by_role('button',name='Next month').click()
                page.wait_for_timeout(350)
            day=root.locator(f'[data-date="{payload["proposed_date"]}"]')
            expect(day).to_have_class('calendar-day partial')
            root.locator('#calendarStart').fill(payload['proposed_start_time']);root.locator('#calendarStart').dispatch_event('change')
            root.locator('#calendarEnd').fill(payload['proposed_end_time']);root.locator('#calendarEnd').dispatch_event('change')
            expect(day).to_have_class('calendar-day booked')
            day.click();expect(root.locator('[data-calendar-details]')).not_to_contain_text('Confidential')
            expect(page.locator('#proposed_date')).to_have_value('')
            root.locator('#calendarStart').fill('17:00');root.locator('#calendarStart').dispatch_event('change')
            root.locator('#calendarEnd').fill('18:00');root.locator('#calendarEnd').dispatch_event('change')
            expect(day).to_be_enabled();expect(root.locator('[data-calendar-state]')).to_contain_text('Availability refreshed')
            day.click();expect(page.locator('#proposed_date')).to_have_value(payload['proposed_date'])
            for width in [1440,768,390]:
                page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(150)
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+2'),width
            page.locator('#event_name').fill('Calendar submitted event');page.locator('#category_id').select_option(payload['category_id'])
            page.locator('#description').fill('Event chosen through calendar')
            page.get_by_role('button',name='Submit Event Request',exact=True).click()
            page.wait_for_url('**/submissions/event/*/success')
            with app.app_context():
                created=EventRequest.query.filter_by(event_name='Calendar submitted event').one()
                assert created.status=='Pending' and created.proposed_start_time.hour==17
            page.goto(base+'/events')
            for _ in range(3):
                if root.locator('[data-calendar-month]').inner_text()==target:break
                root.get_by_role('button',name='Next month').click();page.wait_for_timeout(350)
            root.locator('#calendarStart').fill(payload['proposed_start_time']);root.locator('#calendarStart').dispatch_event('change')
            root.locator('#calendarEnd').fill(payload['proposed_end_time']);root.locator('#calendarEnd').dispatch_event('change')
            expect(day).to_have_class('calendar-day booked')
            fixture.auth('admin')
            assert fixture.post(f'/events/{event_id}',{'status':'Cancelled'}).status_code==200
            page.clock.fast_forward(31000)
            expect(day).not_to_have_class('calendar-day booked')
            assert not errors,errors
            print('PASS cancellation automatically refreshes the calendar after polling')
            print('PASS current/previous/next months; booked and partial slots; privacy; calendar date fills existing form')
            print('PASS event submission remains Pending; desktop/tablet/mobile layout; no JavaScript errors')
            browser.close()
    finally:server.shutdown();fixture.tearDownClass()


if __name__=='__main__':main()
