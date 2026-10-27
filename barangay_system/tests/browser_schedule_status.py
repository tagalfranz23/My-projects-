"""Click the schedule Update form against isolated SQLite fixtures in Edge."""
import sys
import threading
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from app import app
from models import db, Schedule
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect


def main():
    fixture=baseline.SystemAcceptanceTests(); fixture.setUpClass(); fixture.setUp()
    permit_id,_=fixture.create_permit()
    with app.app_context():
        items=[Schedule(requested_by=fixture.ids['resident'], related_type='permit', related_id=permit_id,
            scheduled_datetime=datetime(2026,12,1,9), purpose='Synthetic schedule') for _ in range(2)]
        db.session.add_all(items); db.session.commit()
        ids=[item.id for item in items]
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
            page=browser.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            base=f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/login');page.locator('[name=username]').fill('adminx')
            page.locator('[name=password]').fill('StrongPass123')
            page.locator('button[type=submit]').click();page.wait_for_url('**/dashboard')
            page.goto(base+'/schedules')
            form=page.locator(f'form[action="/schedules/{ids[0]}/status"]')
            assert not form.evaluate('el=>el.checkValidity()')
            for schedule_id,targets in [(ids[0],['Confirmed','Rescheduled','Confirmed','Completed']),
                                       (ids[1],['Cancelled'])]:
                for target in targets:
                    form=page.locator(f'form[action="/schedules/{schedule_id}/status"]')
                    form.locator('[name=status]').select_option(target)
                    with page.expect_navigation() as navigation:
                        form.get_by_role('button',name='Update',exact=True).click()
                    assert navigation.value.status==200
                    expect(page.locator('.flash')).to_contain_text(f'Schedule status updated to {target}.')
                    with app.app_context():assert db.session.get(Schedule,schedule_id).status==target
                    page.reload()
                    print(f'PASS schedule {schedule_id}: saved {target} through Update button and reload')
                expect(page.locator(f'form[action="/schedules/{schedule_id}/status"]')).to_have_count(0)
            assert not errors,errors
            print('PASS terminal schedules have no Update form; no JavaScript errors')
            browser.close()
    finally:
        server.shutdown();fixture.tearDownClass()


if __name__=='__main__':main()
