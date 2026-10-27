"""Real-browser account registration using isolated SQLite records."""
import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from app import app
from models import db, User
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect


def main():
    fixture = baseline.SystemAcceptanceTests()
    fixture.setUpClass()
    fixture.setUp()
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            base = f'http://127.0.0.1:{server.server_port}'
            page.goto(base + '/register')
            data = fixture.registration_data()
            select_names = {'sex', 'civil_status', 'resident_type_id', 'residency_status', 'accepted_id_type_id'}
            skip = {'is_household_head', 'privacy_consent', 'valid_id_file'}
            for key, value in data.items():
                if key in skip:
                    continue
                field = page.locator(f'[name="{key}"]')
                if key in select_names:
                    field.select_option(value)
                else:
                    field.fill(value)
            page.locator('[name=is_family_head]').select_option('No')
            expect(page.locator('#familyMembersSection')).to_be_hidden()
            page.locator('[name=is_family_head]').select_option('Yes')
            for name, birth, relationship in [('Private Browser Child', '2018-04-10', 'Daughter'), ('Private Browser Spouse', '1994-06-12', 'Spouse')]:
                page.get_by_role('button', name='Add Family Member', exact=True).click()
                row = page.locator('.family-member-row').last
                row.locator('[name=family_name]').fill(name)
                row.locator('[name=family_date_of_birth]').fill(birth)
                row.locator('[name=family_relationship]').fill(relationship)
            page.get_by_role('button', name='Add Family Member', exact=True).click()
            page.locator('.family-member-row').last.get_by_role('button', name='Remove', exact=True).click()
            expect(page.locator('.family-member-row')).to_have_count(2)
            assert page.locator('.family-member-row [name*=disability]').count() == 0
            page.locator('[name=has_disability]').select_option('Yes')
            page.locator('[name=disability_type]').select_option('Other')
            expect(page.locator('#residentDisabilityOther')).to_be_visible()
            page.locator('[name=disability_other]').fill('Private optional description')
            page.locator('[name=has_disability]').select_option('No')
            expect(page.locator('#residentDisabilityType')).to_be_hidden()
            page.locator('[name=has_disability]').select_option('Prefer not to say')
            expect(page.locator('#residentDisabilityType')).to_be_hidden()
            page.locator('[name=has_disability]').select_option('Yes')
            page.locator('[name=disability_type]').select_option('Physical Disability')
            page.locator('[name=valid_id_file]').set_input_files({
                'name': 'test-id.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4\nvalidation id'})
            page.locator('[name=privacy_consent]').check()
            for width in [1440, 768, 390]:
                page.set_viewport_size({'width': width, 'height': 1000})
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 2'), width
            page.get_by_role('button', name='Submit Registration for Approval', exact=True).click()
            page.wait_for_url('**/login')
            with app.app_context():
                user = User.query.filter_by(username='newresident').one()
                resident_id = user.id
                assert len(user.resident_profile.family_members) == 2
                assert user.resident_profile.family_members[0].relationship == 'Daughter'
                assert user.resident_profile.disability_type == 'Physical Disability'
                assert User.query.count() == 5
            # Complete the unchanged review routes, then exercise real browser login.
            fixture.auth('admin')
            for suffix in ['verification/id', 'verification/address', 'approval']:
                action = 'approve' if suffix == 'approval' else 'verify'
                assert fixture.post(f'/users/{resident_id}/{suffix}', {'action': action}).status_code == 302
            page.locator('[name=username]').fill('newresident')
            page.locator('[name=password]').fill('Register123')
            page.locator('button[type=submit]').click()
            page.wait_for_url('**/dashboard')
            assert not errors, errors
            print('PASS registration; conditional family/disability fields; add/remove members; real database persistence')
            print('PASS unchanged verification/approval routes; resident login; no family login accounts')
            print('PASS desktop/tablet/mobile layout; no JavaScript errors')
            browser.close()
    finally:
        server.shutdown()
        fixture.tearDownClass()


if __name__ == '__main__':
    main()
