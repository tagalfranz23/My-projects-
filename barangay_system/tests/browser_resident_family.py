"""Staff resident-information form check using an isolated SQLite database."""
import sys
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from app import app
from models import db, User, ResidentFamilyMember
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect


def main():
    fixture=baseline.SystemAcceptanceTests();fixture.setUpClass();fixture.setUp()
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            base=f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/login');page.locator('[name=username]').fill('staffx')
            page.locator('[name=password]').fill('StrongPass123')
            page.locator('button[type=submit]').click();page.wait_for_url('**/dashboard')
            page.goto(base+'/front-desk/residents/new')
            page.locator('[name=full_name]').fill('Family Browser Resident')
            page.locator('[name=address]').fill('44 Test Street');page.locator('[name=purok_zone]').fill('Purok 1')
            page.locator('[name=is_family_head]').select_option('No')
            expect(page.locator('#familyMembersSection')).to_be_hidden()
            page.locator('[name=is_family_head]').select_option('Yes')
            for name,age,gender in [('Child Browser One','8','Female'),('Adult Browser Two','30','Male')]:
                page.get_by_role('button',name='Add Family Member',exact=True).click()
                row=page.locator('.family-member-row').last
                row.locator('[name=family_name]').fill(name);row.locator('[name=family_age]').fill(age)
                row.locator('[name=family_gender]').select_option(gender)
            page.get_by_role('button',name='Add Family Member',exact=True).click()
            page.locator('.family-member-row').last.get_by_role('button',name='Remove',exact=True).click()
            expect(page.locator('.family-member-row')).to_have_count(2)
            assert page.locator('.family-member-row [name*=disability]').count()==0
            page.locator('[name=has_disability]').select_option('No')
            expect(page.locator('#residentDisabilityType')).to_be_hidden()
            page.locator('[name=has_disability]').select_option('Yes')
            page.locator('[name=disability_type]').select_option('Visual Disability')
            page.locator('[name=privacy_consent]').check()
            for width in [1440,768,390]:
                page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(200)
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 2'),width
            page.get_by_role('button',name='Save Resident and Continue to Service').click()
            page.wait_for_url('**/front-desk?resident_id=*')
            page.get_by_role('link',name='View / Update Resident Information').click()
            with app.app_context():
                resident=User.query.filter_by(full_name='Family Browser Resident').one();resident_id=resident.id
                assert resident.resident_profile.has_disability
                assert ResidentFamilyMember.query.count()==2
                assert User.query.count()==5
            expect(page.locator('table')).to_contain_text('Child Browser One')
            expect(page.locator('[data-remove-family]')).to_have_count(0)
            page.locator('[name=disability_type]').select_option('Other')
            expect(page.locator('#residentDisabilityOther')).to_be_visible()
            page.locator('[name=disability_other]').fill('Optional private description')
            page.locator('[name=family_age]').first.fill('9')
            page.get_by_role('button',name='Save Resident Information',exact=True).click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('main')).to_contain_text('Optional private description')
            page.get_by_role('button',name='Save Resident Information',exact=True).click()
            page.wait_for_load_state('networkidle')
            with app.app_context():assert ResidentFamilyMember.query.count()==2
            page.locator('[name=has_disability]').select_option('No')
            page.locator('[name=is_family_head]').select_option('No')
            expect(page.locator('#familyMembersSection')).to_be_hidden()
            page.get_by_role('button',name='Save Resident Information',exact=True).click()
            page.wait_for_load_state('networkidle')
            with app.app_context():
                profile=db.session.get(User,resident_id).resident_profile
                assert not profile.has_disability and profile.disability_type is None and profile.disability_other is None
                assert len(profile.family_members)==2
                assert profile.family_members[0].age==9
            assert not errors,errors
            print('PASS head/non-head visibility; add/remove unsaved members; resident-only disability fields')
            print('PASS Visual/Other/No disability; private details; edit/repeat without duplicates; saved members preserved')
            print('PASS desktop/tablet/mobile layout; no JavaScript errors; no family login accounts')
            browser.close()
    finally:
        server.shutdown();fixture.tearDownClass()


if __name__=='__main__':main()
