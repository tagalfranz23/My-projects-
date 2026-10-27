"""Confidential staff resident-information intake, editing and preservation."""
import hashlib
import unittest
from pathlib import Path
from sqlalchemy import inspect
import test_system as baseline
from app import app
from models import (db, User, ResidentProfile, ResidentFamilyMember, Household, HouseholdMember, ActivityLog)
from migration import upgrade_legacy_schema
from services.resident_family import DISABILITY_TYPES


class ResidentFamilyTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    post = baseline.SystemAcceptanceTests.post
    setUpClass = classmethod(lambda cls: app.config.update(TESTING=True))

    def payload(self, **extra):
        values = dict(full_name='Family Head Resident', address='33 Family Street', purok_zone='Purok 3',
            privacy_consent='1', family_information='1', is_family_head='Yes', has_disability='Yes',
            disability_type='Visual Disability', family_id=['',''], family_name=['Private Child One','Private Adult Two'],
            family_age=['8','42'], family_gender=['Female','Male'])
        values.update(extra)
        return values

    def create(self, **extra):
        self.auth('staff')
        response=self.post('/front-desk/residents/new',self.payload(**extra))
        self.assertEqual(response.status_code,302)
        with app.app_context():return User.query.filter_by(full_name='Family Head Resident').one().id

    def test_create_head_multiple_members_and_confidential_details(self):
        resident_id=self.create()
        with app.app_context():
            profile=db.session.get(User,resident_id).resident_profile
            self.assertTrue(profile.is_household_head)
            self.assertTrue(profile.has_disability)
            self.assertEqual(profile.disability_type,'Visual Disability')
            self.assertEqual(len(profile.family_members),2)
            self.assertEqual(User.query.count(),5)  # One resident; no child/adult login accounts.
            self.assertTrue(all(member.head_profile_id==profile.id for member in profile.family_members))
            self.assertTrue(all(member.created_by==self.ids['staff'] for member in profile.family_members))
            self.assertEqual(profile.approval_status,'Pending')
        for role in ['admin','staff']:
            self.auth(role)
            response=self.client.get(f'/front-desk/residents/{resident_id}/information')
            self.assertEqual(response.status_code,200)
            self.assertIn('no-store',response.headers['Cache-Control'])
            for expected in [b'Private Child One',b'Private Adult Two',b'Visual Disability']:
                self.assertIn(expected,response.data)
        self.auth('resident')
        self.assertEqual(self.client.get(f'/front-desk/residents/{resident_id}/information').status_code,403)
        self.assertEqual(self.post(f'/front-desk/residents/{resident_id}/information',self.payload()).status_code,403)
        self.assertNotIn(b'Private Child One',self.client.get('/profile').data)
        self.auth('admin')
        self.assertNotIn(b'Private Child One',self.client.get('/reports').data)
        with app.app_context():
            self.assertFalse(any('Visual Disability' in (row.details or '') or 'Private Child' in (row.details or '')
                                 for row in ActivityLog.query.all()))

    def test_no_disability_discards_type_and_no_head_discards_new_rows(self):
        resident_id=self.create(is_family_head='No',has_disability='No',disability_type='Other',disability_other='Ignore this')
        with app.app_context():
            profile=db.session.get(User,resident_id).resident_profile
            self.assertFalse(profile.is_household_head)
            self.assertFalse(profile.has_disability)
            self.assertIsNone(profile.disability_type)
            self.assertIsNone(profile.disability_other)
            self.assertEqual(ResidentFamilyMember.query.count(),0)

    def test_other_and_prefer_not_specify_and_clearing_on_edit(self):
        resident_id=self.create(disability_type='Other',disability_other='Private optional description')
        with app.app_context():
            self.assertEqual(db.session.get(User,resident_id).resident_profile.disability_other,'Private optional description')
        url=f'/front-desk/residents/{resident_id}/information'
        for kind in DISABILITY_TYPES:
            self.assertEqual(self.post(url,dict(family_information='1',is_family_head='No',has_disability='Yes',disability_type=kind)).status_code,302)
            with app.app_context():self.assertEqual(db.session.get(User,resident_id).resident_profile.disability_type,kind)
        self.post(url,dict(family_information='1',is_family_head='No',has_disability='No',disability_type='Other',disability_other='Must clear'))
        with app.app_context():
            profile=db.session.get(User,resident_id).resident_profile
            self.assertFalse(profile.has_disability)
            self.assertIsNone(profile.disability_type)
            self.assertIsNone(profile.disability_other)
            self.assertEqual(len(profile.family_members),2)

    def test_edit_preserves_existing_rows_and_household_links_without_duplicates(self):
        self.auth('staff')
        with app.app_context():
            resident=db.session.get(User,self.ids['resident'])
            household=Household(household_number='Preserve household',head_resident_id=resident.id)
            db.session.add(household);db.session.flush()
            membership=HouseholdMember(household_id=household.id,resident_id=resident.id)
            db.session.add(membership);db.session.commit()
            household_id=household.id; membership_id=membership.id
        url=f"/front-desk/residents/{self.ids['resident']}/information"
        data=self.payload()
        for _ in range(2):self.assertEqual(self.post(url,data).status_code,302)
        with app.app_context():
            members=ResidentFamilyMember.query.order_by(ResidentFamilyMember.id).all()
            self.assertEqual(len(members),2)
            ids=[member.id for member in members]
            self.assertTrue(all(member.household_id==household_id for member in members))
        data.update(family_id=[str(value) for value in ids],family_age=['9','43'])
        self.assertEqual(self.post(url,data).status_code,302)
        # An omitted saved row and selecting No must never delete family information.
        self.assertEqual(self.post(url,dict(family_information='1',is_family_head='No',has_disability='No')).status_code,302)
        with app.app_context():
            self.assertEqual([member.id for member in ResidentFamilyMember.query.order_by(ResidentFamilyMember.id)],[*ids])
            self.assertEqual([member.age for member in ResidentFamilyMember.query.order_by(ResidentFamilyMember.id)],[9,43])
            self.assertEqual(db.session.get(Household,household_id).head_resident_id,self.ids['resident'])
            self.assertEqual(db.session.get(HouseholdMember,membership_id).membership_status,'Active')
            self.assertEqual(db.session.get(User,self.ids['resident']).resident_profile.household_number,'HH-001')

    def test_invalid_entries_rollback_and_cross_resident_ids_are_rejected(self):
        self.auth('staff')
        invalid = [dict(family_age=['-1','42']),dict(family_gender=['Other','Male']),
            dict(family_name=['Same',' same '],family_age=['8','8'],family_gender=['Male','Male']),
            dict(has_disability='Yes',disability_type='Invalid'),dict(is_family_head=''),
            dict(family_age=['8']),dict(family_id=['9999',''])]
        for fields in invalid:
            with self.subTest(fields=fields):
                response=self.post('/front-desk/residents/new',self.payload(**fields))
                self.assertEqual(response.status_code,200)
                with app.app_context():
                    self.assertEqual(User.query.count(),4)
                    self.assertEqual(ResidentFamilyMember.query.count(),0)
        resident_id=self.create()
        with app.app_context():member_id=ResidentFamilyMember.query.first().id
        data=dict(family_information='1',is_family_head='Yes',has_disability='No',family_id=[str(member_id)],
                  family_name=['Attempted replacement'],family_age=['12'],family_gender=['Male'])
        self.assertEqual(self.post(f"/front-desk/residents/{self.ids['other']}/information",data).status_code,200)
        with app.app_context():
            self.assertEqual(db.session.get(ResidentFamilyMember,member_id).full_name,'Private Child One')
            self.assertEqual(ResidentFamilyMember.query.count(),2)

    def test_additive_migration_preserves_existing_records_and_protected_forms(self):
        resident_id=self.create()
        with app.app_context():
            upgrade_legacy_schema();upgrade_legacy_schema()
            self.assertEqual(ResidentFamilyMember.query.count(),2)
            self.assertIsNotNone(db.session.get(User,resident_id))
            columns={row['name'] for row in inspect(db.engine).get_columns('resident_profiles')}
            self.assertTrue({'has_disability','disability_type','disability_other'} <= columns)
        root=Path(__file__).resolve().parents[1]
        for name,expected in [('records.html','4818cb4314d3c54fed663d563e8422f1e58ecb5aed5b44d8134b1fa384da3f91')]:
            content=(root/'templates'/name).read_bytes()
            if name=='records.html':
                content=content.decode('utf-8').replace('\r\n','\n').split("{% if kind=='permit' %}",1)[1].split("{% elif kind=='event' %}",1)[0].encode()
            self.assertEqual(hashlib.sha256(content).hexdigest(),expected)


if __name__=='__main__':unittest.main()
