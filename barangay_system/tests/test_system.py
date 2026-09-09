import os, tempfile, unittest
db_file=os.path.join(tempfile.gettempdir(),"barangay_system_tests.db")
if os.path.exists(db_file): os.remove(db_file)
os.environ["DATABASE_URL"]="sqlite:///"+db_file.replace("\\","/")
os.environ["SECRET_KEY"]="test-secret"
from app import app
from models import db, User, PermitType, PermitApplication, EventRequest, BlotterCase, ActivityLog, PasswordResetToken, Announcement, Notification
from migration import upgrade_legacy_schema
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from datetime import datetime, date
from unittest.mock import patch

class SystemTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  app.config.update(TESTING=True); cls.client=app.test_client()
  with app.app_context():
   db.drop_all(); db.create_all()
   for username,role,email in (("adminx","admin","a@test.local"),("staffx","staff","s@test.local"),("resx","resident","r@test.local"),("resy","resident","y@test.local")):
    u=User(username=username,role=role,email=email,full_name=username); u.set_password("StrongPass123"); db.session.add(u)
   db.session.add(PermitType(name="Clearance")); db.session.commit()
 @classmethod
 def tearDownClass(cls):
  with app.app_context(): db.session.remove(); db.drop_all(); db.engine.dispose()
  if os.path.exists(db_file): os.remove(db_file)
 def auth(self,username):
  with app.app_context(): uid=User.query.filter_by(username=username).first().id
  with self.client.session_transaction() as s: s["user_id"]=uid; s["csrf_token"]="csrf"
 def post(self,url,data): return self.client.post(url,data={**data,"csrf_token":"csrf"},follow_redirects=False)
 def test_role_access_boundaries(self):
  self.auth("staffx")
  self.assertEqual(self.client.get("/reports").status_code,403); self.assertEqual(self.client.get("/activity-logs").status_code,403); self.assertEqual(self.client.get("/configuration").status_code,403)
  self.assertEqual(self.client.get("/users").status_code,200)
  self.auth("resx"); self.assertEqual(self.client.get("/users").status_code,403)
  self.auth("adminx"); self.assertEqual(self.client.get("/reports").status_code,200); self.assertEqual(self.client.get("/activity-logs").status_code,200)
 def test_sqlite_durability_pragmas_and_health(self):
  with app.app_context():
   self.assertEqual(db.engine.dialect.name,"sqlite")
   self.assertEqual(db.session.execute(text("PRAGMA foreign_keys")).scalar(),1)
   self.assertEqual(db.session.execute(text("PRAGMA journal_mode")).scalar().lower(),"wal")
   self.assertGreaterEqual(db.session.execute(text("PRAGMA busy_timeout")).scalar(),30000)
   self.assertEqual(db.session.execute(text("PRAGMA integrity_check")).scalar().lower(),"ok")
  response=self.client.get("/healthz"); self.assertEqual(response.status_code,200); self.assertEqual(response.get_json()["database"],"sqlite")
 def test_sqlite_foreign_keys_and_idempotent_upgrade(self):
  with app.app_context():
   admin=User.query.filter_by(username="adminx").first(); original_hash=admin.password_hash; before_users=User.query.count()
   upgrade_legacy_schema(); upgrade_legacy_schema()
   self.assertEqual(User.query.count(),before_users); self.assertEqual(User.query.filter_by(username="adminx").first().password_hash,original_hash)
   invalid=PermitApplication(applicant_id=999999,permit_type_id=999999,purpose="Must fail")
   db.session.add(invalid)
   with self.assertRaises(IntegrityError): db.session.commit()
   db.session.rollback()
   self.assertIsNotNone(User.query.filter_by(username="adminx").first())
 def test_use_case_diagram_actor_flows(self):
  self.auth("resx")
  for url in ("/profile","/announcements","/status","/permits","/events","/blotters","/schedules","/notifications"):
   self.assertEqual(self.client.get(url).status_code,200,url)
  self.assertEqual(self.post("/notifications/send",{"user_id":1,"title":"x","message":"x"}).status_code,403)
  self.auth("staffx")
  for url in ("/profile","/announcements","/permits","/events","/blotters","/schedules","/notifications","/users"):
   self.assertEqual(self.client.get(url).status_code,200,url)
  for url in ("/reports","/activity-logs","/configuration"):
   self.assertEqual(self.client.get(url).status_code,403,url)
  self.auth("adminx")
  self.assertEqual(self.post("/announcements",{"title":"Service update","body":"Barangay office schedule"}).status_code,200)
  with app.app_context():
   self.assertTrue(Announcement.query.filter_by(title="Service update").first()); resident=User.query.filter_by(username="resx").first(); resident_id=resident.id
  self.assertEqual(self.post("/notifications/send",{"user_id":resident_id,"channel":"in_app","title":"Update","message":"Your status changed."}).status_code,302)
  with app.app_context(): self.assertTrue(Notification.query.filter_by(user_id=resident_id,title="Update").first())
  self.assertEqual(self.post(f"/users/{resident_id}/role",{"role":"staff"}).status_code,302)
  with app.app_context(): self.assertEqual(db.session.get(User,resident_id).role,"staff")
  self.assertEqual(self.post(f"/users/{resident_id}/role",{"role":"resident"}).status_code,302)
 def test_permit_ownership_and_staff_cannot_approve(self):
  with app.app_context():
   owner=User.query.filter_by(username="resx").first(); pt=PermitType.query.first(); p=PermitApplication(applicant_id=owner.id,permit_type_id=pt.id,purpose="Test"); db.session.add(p); db.session.commit(); pid=p.id
  self.auth("resy"); self.assertEqual(self.client.get(f"/permits/{pid}").status_code,403)
  self.auth("staffx"); self.assertEqual(self.post(f"/permits/{pid}",{"status":"Approved"}).status_code,400)
  self.assertEqual(self.post(f"/permits/{pid}",{"status":"Under Review"}).status_code,200)
 def test_submission_receipts_are_real_owned_single_page_pdfs(self):
  with app.app_context():
   owner=User.query.filter_by(username="resx").first(); other=User.query.filter_by(username="resy").first(); pt=PermitType.query.first()
   permit=PermitApplication(applicant_id=owner.id,permit_type_id=pt.id,purpose="Receipt test")
   event=EventRequest(requester_id=owner.id,event_name="Community Meeting",event_type="Community",description="Receipt test",proposed_date=date.today(),proposed_location="Hall",expected_attendees=10)
   blotter=BlotterCase(complainant_id=owner.id,respondent_name="Party",incident_type="Incident",incident_date=datetime.utcnow(),incident_location="Hall",narrative="Confidential detail")
   db.session.add_all([permit,event,blotter]); db.session.commit(); ids={"permit":permit.id,"event":event.id,"blotter":blotter.id}; permit_ref=permit.reference_no
  self.auth("resx")
  for kind,record_id in ids.items():
   response=self.client.get(f"/receipts/{kind}/{record_id}.pdf"); self.assertEqual(response.status_code,200); self.assertEqual(response.data[:4],b"%PDF"); self.assertLessEqual(response.data.count(b"/Type /Page"),2); self.assertIn(b"SUBMISSION ACKNOWLEDGMENT",response.data)
  self.auth("resy"); self.assertEqual(self.client.get(f"/receipts/permit/{ids['permit']}.pdf").status_code,403)
  with patch("app.generate_submission_receipt",side_effect=RuntimeError("test failure")):
   self.auth("resx"); self.assertEqual(self.client.get(f"/receipts/permit/{ids['permit']}.pdf").status_code,500)
  with app.app_context(): self.assertIsNotNone(db.session.get(PermitApplication,ids["permit"])); self.assertEqual(db.session.get(PermitApplication,ids["permit"]).reference_no,permit_ref)
  with app.app_context(): db.session.get(PermitApplication,ids["permit"]).status="Approved"; db.session.commit()
  self.auth("resx"); approved=self.client.get(f"/permits/{ids['permit']}/approved-document"); self.assertEqual(approved.status_code,200); self.assertEqual(approved.data[:4],b"%PDF")
 def test_submission_redirects_only_after_successful_commit(self):
  self.auth("resx")
  with app.app_context(): pt=PermitType.query.first(); before=PermitApplication.query.count(); pt_id=pt.id
  response=self.post("/permits",{"permit_type_id":pt_id,"purpose":"Barangay requirement"})
  self.assertEqual(response.status_code,302); self.assertIn("/submissions/permit/",response.headers["Location"])
  with app.app_context(): self.assertEqual(PermitApplication.query.count(),before+1)
  failed=self.post("/permits",{"permit_type_id":pt_id,"purpose":""}); self.assertEqual(failed.status_code,200)
  with app.app_context(): self.assertEqual(PermitApplication.query.count(),before+1)
 def test_forgot_password_token_single_use(self):
  self.client.get("/forgot-password")
  with self.client.session_transaction() as s: token=s["csrf_token"]
  response=self.client.post("/forgot-password",data={"email":"a@test.local","csrf_token":token})
  self.assertEqual(response.status_code,200)
  with app.app_context(): record=PasswordResetToken.query.order_by(PasswordResetToken.id.desc()).first(); self.assertIsNotNone(record); self.assertNotIn("StrongPass123",record.token_hash)
 def test_all_primary_pages_render(self):
  self.auth("adminx")
  for url in ("/dashboard","/permits","/events","/blotters","/schedules","/notifications","/users","/reports","/activity-logs","/configuration"):
   self.assertEqual(self.client.get(url).status_code,200,url)
  self.assertEqual(self.post("/reports/export/csv",{}).status_code,200)
  pdf=self.post("/reports/export/pdf",{}); self.assertEqual(pdf.status_code,200); self.assertEqual(pdf.data[:4],b"%PDF"); pdf.close()
 def test_completed_reset_is_single_use_and_keeps_admin_role(self):
  with app.app_context():
   user=User.query.filter_by(username="adminx").first(); raw,record=PasswordResetToken.issue(user); db.session.commit(); uid=user.id
  self.client.get(f"/reset-password/{raw}")
  with self.client.session_transaction() as s: csrf=s["csrf_token"]
  response=self.client.post(f"/reset-password/{raw}",data={"csrf_token":csrf,"password":"NewSecure456","confirm_password":"NewSecure456"})
  self.assertEqual(response.status_code,302); self.assertEqual(self.client.get(f"/reset-password/{raw}").status_code,302)
  with app.app_context():
   user=db.session.get(User,uid); self.assertEqual(user.role,"admin"); self.assertTrue(user.check_password("NewSecure456")); self.assertFalse(user.check_password("StrongPass123")); self.assertTrue(ActivityLog.query.filter_by(action="PASSWORD_RESET_COMPLETED",user_id=uid).count())

if __name__=="__main__": unittest.main()
