"""Account registration, private family details and lossless legacy migration."""
import unittest
from datetime import date, timedelta
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateTable
import test_system as baseline
from app import app
from migration import upgrade_legacy_schema
from models import db, User, ResidentFamilyMember, ActivityLog
from services.analytics import population_sections


class RegistrationFamilyTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    clear_auth = baseline.SystemAcceptanceTests.clear_auth
    post = baseline.SystemAcceptanceTests.post
    registration_data = baseline.SystemAcceptanceTests.registration_data
    setUpClass = classmethod(lambda cls: app.config.update(TESTING=True))

    def payload(self, username='newresident', **changes):
        data = self.registration_data(username, username + '@test.local')
        data.update(family_information='1', is_family_head='Yes', has_disability='Yes',
            disability_type='Physical Disability', family_name=['Private Child', 'Private Spouse'],
            family_date_of_birth=['2018-04-10', '1994-06-12'], family_relationship=['Daughter', 'Spouse'])
        data.update(changes)
        return data

    def register(self, **changes):
        self.clear_auth()
        response = self.post('/register', self.payload(**changes))
        self.assertEqual(response.status_code, 302, response.data.decode()[:200])
        with app.app_context():
            return User.query.filter_by(username=changes.get('username', 'newresident')).one().id

    def test_complete_registration_approval_login_and_authorized_details(self):
        resident_id = self.register()
        with app.app_context():
            profile = db.session.get(User, resident_id).resident_profile
            self.assertEqual(profile.approval_status, 'Pending')
            self.assertTrue(profile.is_household_head)
            self.assertTrue(profile.has_disability)
            self.assertEqual(profile.disability_type, 'Physical Disability')
            self.assertEqual(User.query.count(), 5)
            self.assertEqual(len(profile.family_members), 2)
            child = profile.family_members[0]
            self.assertEqual(child.date_of_birth, date(2018, 4, 10))
            self.assertEqual(child.relationship, 'Daughter')
            self.assertIsNone(child.gender)  # Never guess an uncollected gender.
            self.assertEqual(child.head_profile_id, profile.id)
            self.assertEqual(child.created_by, resident_id)
            self.assertFalse(any('Private Child' in (log.details or '') or 'Physical Disability' in (log.details or '')
                                 for log in ActivityLog.query.all()))
        response = self.post('/login', dict(username='newresident', password='Register123'), follow_redirects=True)
        self.assertIn(b'still being verified', response.data)
        for role in ['resident', 'other']:
            self.auth(role)
            self.assertEqual(self.client.get(f'/users/{resident_id}').status_code, 403)
            self.assertEqual(self.client.get(f'/front-desk/residents/{resident_id}/information').status_code, 403)
        self.auth('admin')
        details = self.client.get(f'/users/{resident_id}')
        for value in [b'Private Child', b'2018-04-10', b'Daughter', b'Physical Disability']:
            self.assertIn(value, details.data)
        self.assertIn('no-store', details.headers['Cache-Control'])
        for suffix in ['verification/id', 'verification/address', 'approval']:
            action = 'approve' if suffix == 'approval' else 'verify'
            self.assertEqual(self.post(f'/users/{resident_id}/{suffix}', dict(action=action)).status_code, 302)
        with app.app_context():
            totals = dict(population_sections()[0]['rows'])
            self.assertEqual(totals['Total Population'], 4)  # Excludes the two family members.
            self.assertEqual(totals['Residents with Disabilities'], 1)
        self.clear_auth()
        response = self.post('/login', dict(username='newresident', password='Register123'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/dashboard'))
        with self.client.session_transaction() as session:
            self.assertEqual(session['user_id'], resident_id)

    def test_optional_disability_type_other_no_and_non_disclosure(self):
        for index, (answer, kind, description, expected) in enumerate([
            ('Yes', '', '', None), ('Yes', 'Other', 'Optional private description', True),
            ('No', 'Other', 'Discard', False), ('Prefer not to say', 'Other', 'Discard', None)]):
            with self.subTest(answer=answer, kind=kind):
                resident_id = self.register(username=f'choice{index}', has_disability=answer,
                    disability_type=kind, disability_other=description, is_family_head='No')
                with app.app_context():
                    profile = db.session.get(User, resident_id).resident_profile
                    self.assertFalse(profile.is_household_head)
                    self.assertEqual(profile.family_members, [])
                    self.assertIs(profile.has_disability, True if answer == 'Yes' else expected)
                    self.assertEqual(profile.disability_declined, answer == 'Prefer not to say')
                    self.assertEqual(profile.disability_type, kind or None if answer == 'Yes' else None)
                    self.assertEqual(profile.disability_other, description if kind == 'Other' and answer == 'Yes' else None)

    def test_invalid_family_data_rejected_without_partial_accounts(self):
        invalid = [
            dict(family_date_of_birth=['not-a-date', '1994-06-12']),
            dict(family_date_of_birth=[(date.today() + timedelta(days=1)).isoformat(), '1994-06-12']),
            dict(family_relationship=['', 'Spouse']), dict(family_relationship=['Daughter']),
            dict(family_name=['Private Child', ' private   child '],
                 family_date_of_birth=['2018-04-10'] * 2),
            dict(family_name=['New Resident'], family_date_of_birth=['1995-05-10'], family_relationship=['Self']),
            dict(is_family_head='invalid'), dict(has_disability='invalid'),
            dict(disability_type='invalid'), dict(disability_type='Other', disability_other='x' * 301),
            dict(family_name=['x' * 151, 'Private Spouse'])]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.clear_auth()
                response = self.post('/register', self.payload(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'flash-danger', response.data)
                self.assertIn('no-store', response.headers['Cache-Control'])
                with app.app_context():
                    self.assertEqual(User.query.count(), 4)
                    self.assertEqual(ResidentFamilyMember.query.count(), 0)

    def test_staff_edits_birthdate_members_without_duplicates_or_lost_details(self):
        resident_id = self.register(has_disability='Prefer not to say')
        with app.app_context():
            members = db.session.get(User, resident_id).resident_profile.family_members
            ids = [member.id for member in members]
        self.auth('staff')
        url = f'/front-desk/residents/{resident_id}/information'
        response = self.client.get(url)
        self.assertIn(b'Prefer not to say', response.data)
        self.assertIn(b'family_date_of_birth', response.data)
        data = dict(family_information='1', is_family_head='Yes', has_disability='Prefer not to say',
            family_id=[str(value) for value in ids], family_name=['Private Child', 'Private Spouse'],
            family_date_of_birth=['2018-04-10', '1994-06-12'], family_relationship=['Daughter', 'Spouse'],
            family_age=['0', '0'], family_gender=['', ''])
        for _ in range(2):
            self.assertEqual(self.post(url, data).status_code, 302)
        self.assertEqual(self.post(url, dict(family_information='1', is_family_head='No', has_disability='No')).status_code, 302)
        with app.app_context():
            members = ResidentFamilyMember.query.order_by(ResidentFamilyMember.id).all()
            self.assertEqual([member.id for member in members], ids)
            self.assertEqual(members[0].relationship, 'Daughter')
            self.assertEqual(members[0].date_of_birth, date(2018, 4, 10))
            self.assertEqual(members[0].updated_by, self.ids['staff'])
            self.assertIsNone(members[0].gender)
            self.assertGreater(members[0].age, 0)  # Derived from DOB, not posted stale age.

    def test_legacy_family_migration_is_repeatable_and_preserves_every_value(self):
        with app.app_context():
            # All DDL below affects only the isolated acceptance-test SQLite file.
            table = ResidentFamilyMember.__table__
            ddl = str(CreateTable(table).compile(db.engine))
            ddl = ddl.replace('age INTEGER,', 'age INTEGER NOT NULL,').replace('gender VARCHAR(6),', 'gender VARCHAR(6) NOT NULL,')
            ddl = ddl.replace('\tdate_of_birth DATE, \n', '').replace('\trelationship VARCHAR(80), \n', '')
            db.session.execute(text('DROP TABLE resident_family_members'))
            db.session.execute(text(ddl))
            # Insert using legacy columns, before the ORM's new columns exist.
            db.session.execute(text("INSERT INTO resident_family_members (head_profile_id, full_name, age, gender, member_key, created_by, updated_by, created_at, updated_at) VALUES (:profile, 'Existing private family member', 45, 'Female', 'legacy-key', :actor, :actor, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                dict(profile=db.session.get(User, self.ids['resident']).resident_profile.id, actor=self.ids['staff']))
            db.session.commit()
            legacy_columns = ', '.join(column['name'] for column in inspect(db.engine).get_columns(table.name))
            before = db.session.execute(text(f'SELECT {legacy_columns} FROM resident_family_members')).all()
            self.assertFalse(next(c for c in inspect(db.engine).get_columns(table.name) if c['name']=='gender')['nullable'])
            upgrade_legacy_schema()
            upgrade_legacy_schema()
            after = db.session.execute(text(f'SELECT {legacy_columns} FROM resident_family_members')).all()
            self.assertEqual(before, after)
            columns = {c['name']:c for c in inspect(db.engine).get_columns(table.name)}
            self.assertTrue(columns['gender']['nullable'])
            self.assertTrue({'date_of_birth', 'relationship'} <= columns.keys())
            self.assertEqual(db.session.execute(text('PRAGMA foreign_key_check')).all(), [])
        self.register()  # New DOB-based entries also save after upgrading old constraints.


if __name__ == '__main__':
    unittest.main()
