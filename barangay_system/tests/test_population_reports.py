"""Database-based population rules, aggregate privacy, and web/PDF parity."""
import re
import json
import unittest
from io import BytesIO
from pypdf import PdfReader
import test_system as baseline
from app import app
from models import (db, User, ResidentProfile, ResidentFamilyMember, Household, HouseholdMember)
from services.analytics import build_scope_reports, population_sections
from services.resident_family import member_key


class PopulationReportTests(unittest.TestCase):
    setUp=baseline.SystemAcceptanceTests.setUp
    auth=baseline.SystemAcceptanceTests.auth
    post=baseline.SystemAcceptanceTests.post
    setUpClass=classmethod(lambda cls:app.config.update(TESTING=True))

    def fixture(self):
        with app.app_context():
            staff=db.session.get(User,self.ids['staff']).resident_profile
            staff.has_disability=True;staff.disability_type='Visual Disability'
            resident=db.session.get(User,self.ids['resident']).resident_profile
            resident.sex='Female';resident.has_disability=False
            other=db.session.get(User,self.ids['other']).resident_profile
            other.sex=None;other.has_disability=None
            for i,sex,status,active,disability,kind,online in [
                (1,'Female','Approved',True,True,'Other',False),
                (2,' male ','Approved',True,True,None,True),
                (3,'Male','Pending',True,True,'Hearing Disability',True),
                (4,'Female','Rejected',True,False,None,True),
                (5,'Male','Approved',False,True,'Physical Disability',True)]:
                user=User(username=f'population{i}',full_name=f'Confidential Resident {i}',role='resident',
                    is_active=active,online_access_enabled=online,password_hash='unused test hash')
                db.session.add(user);db.session.flush()
                db.session.add(ResidentProfile(user_id=user.id,sex=sex,approval_status=status,
                    has_disability=disability,disability_type=kind,disability_other='Confidential diagnosis' if kind=='Other' else None))
            # Children and memberships must not multiply the resident population.
            db.session.add(ResidentFamilyMember(head_profile_id=resident.id,full_name='Confidential Child',age=8,
                gender='Female',member_key=member_key('Confidential Child',8,'Female'),created_by=self.ids['staff'],updated_by=self.ids['staff']))
            for number in ['Population household A','Population household B']:
                household=Household(household_number=number)
                db.session.add(household);db.session.flush()
                db.session.add(HouseholdMember(household_id=household.id,resident_id=self.ids['resident']))
            db.session.commit()

    def test_unique_approved_active_population_and_shared_denominators(self):
        self.fixture()
        with app.app_context():
            sections={s['title']:s for s in population_sections()}
            self.assertEqual(dict(sections['Population Statistics']['rows']),{
                'Total Population':5,'Male Residents':2,'Female Residents':2,
                'Residents with Disabilities':3,'Residents without Disabilities':1,
                'Gender not recorded / unspecified':1,'Disability status not recorded':1})
            self.assertEqual(sections['Gender Distribution']['rows'],[('Male',2),('Female',2)])
            self.assertEqual(sections['Gender Distribution']['percentages'],{'Male':50.0,'Female':50.0})
            self.assertEqual(dict(sections['Disability Distribution by Type']['rows']),{
                'Visual Disability':1,'Other':1,'Not recorded / unspecified':1})
            self.assertEqual(sum(v for _,v in sections['Disability Statistics']['rows']),4)
            overall=build_scope_reports()['categories'][0]
            self.assertEqual([s for s in overall['sections'] if s['title']=='Population Statistics'][0],sections['Population Statistics'])

    def test_web_and_protected_pdf_match_counts_and_exclude_personal_details(self):
        self.fixture();self.auth('admin')
        page=self.client.get('/reports');self.assertEqual(page.status_code,200)
        html=page.data.decode()
        for label,value in [('Total Population',5),('Male Residents',2),('Female Residents',2),
                            ('Residents with Disabilities',3),('Residents without Disabilities',1)]:
            self.assertIn(f'<td>{label}</td><td>{value}</td>',html)
        self.assertIn('2 (50.00%)',html)
        charts={data['title']:data for data in
                (json.loads(value) for value in re.findall(r"data-chart='([^']+)'",html))}
        self.assertEqual(charts['Gender Distribution']['rows'],[['Male',2],['Female',2]])
        self.assertEqual(sum(value for _,value in charts['Disability Distribution by Type']['rows']),3)
        response=self.post('/reports/export/pdf');self.assertEqual(response.status_code,200)
        pdf=PdfReader(BytesIO(response.data));text='\n'.join(p.extract_text() for p in pdf.pages)
        self.assertTrue(pdf.is_encrypted)
        with app.app_context():
            for section in population_sections():
                self.assertIn(section['title'],text)
                for label,value in section['rows']:
                    displayed=f"{value} ({section['percentages'][label]:.2f}%)" if 'percentages' in section else str(value)
                    self.assertRegex(text,re.escape(label)+r'\s+'+re.escape(displayed))
        for private in ['Confidential Resident','Confidential diagnosis','Confidential Child','password_hash']:
            self.assertNotIn(private,html);self.assertNotIn(private,text)
        for kept in ['Permit Reports','Event Request Reports','Blotter Reports']:
            self.assertIn(kept,html)
        for role in ['resident','staff']:
            self.auth(role)
            self.assertEqual(self.client.get('/reports').status_code,403)
            self.assertEqual(self.post('/reports/export/pdf').status_code,403)

    def test_missing_gender_disability_type_and_empty_eligible_population(self):
        with app.app_context():
            for profile in ResidentProfile.query.all():
                profile.sex=' ';profile.has_disability=None
            db.session.commit()
            sections={s['title']:s for s in population_sections()}
            self.assertEqual(dict(sections['Population Statistics']['rows'])['Total Population'],3)
            self.assertEqual(sections['Gender Distribution']['percentages'],{'Male':0,'Female':0})
            self.assertEqual(dict(sections['Population Statistics']['rows'])['Disability status not recorded'],3)
            self.assertEqual(sections['Disability Distribution by Type']['rows'],[])
            ResidentProfile.query.update({'approval_status':'Pending'});db.session.commit()
            sections=population_sections()
            self.assertTrue(all(v==0 for _,v in sections[0]['rows']))
        self.auth('admin')
        self.assertEqual(self.client.get('/reports').status_code,200)
        self.assertEqual(self.post('/reports/export/pdf').status_code,200)


if __name__=='__main__':unittest.main()
