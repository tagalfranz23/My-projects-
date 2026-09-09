import os,sys

# Browser QA is deliberately isolated from the configured application database.
# The filename must visibly identify it as a QA/test database.
qa_database_url=os.environ.get("QA_DATABASE_URL","")
if not qa_database_url.lower().startswith("sqlite:") or not any(marker in qa_database_url.lower() for marker in ("qa","test")):
    raise RuntimeError("Set QA_DATABASE_URL to an isolated SQLite file whose name contains 'qa' or 'test'.")
os.environ["DATABASE_URL"]=qa_database_url

sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
from app import app
from models import db,User,Notification,ActivityLog,PasswordResetToken,PermitApplication,EventRequest,BlotterCase,Schedule,Report,Announcement
from migration import upgrade_legacy_schema

PREFIX="__browser_qa_"
def cleanup_user(user):
    PermitApplication.query.filter((PermitApplication.applicant_id!=user.id)&((PermitApplication.decided_by==user.id)|(PermitApplication.reviewed_by==user.id))).update({"decided_by":None,"reviewed_by":None},synchronize_session=False)
    EventRequest.query.filter((EventRequest.requester_id!=user.id)&(EventRequest.reviewed_by==user.id)).update({"reviewed_by":None},synchronize_session=False)
    BlotterCase.query.filter((BlotterCase.complainant_id!=user.id)&(BlotterCase.updated_by==user.id)).update({"updated_by":None},synchronize_session=False)
    Schedule.query.filter((Schedule.requested_by!=user.id)&(Schedule.confirmed_by==user.id)).update({"confirmed_by":None},synchronize_session=False)
    Notification.query.filter((Notification.user_id==user.id)|(Notification.recipient_user_id==user.id)).delete(synchronize_session=False)
    Announcement.query.filter_by(created_by=user.id).delete(synchronize_session=False)
    Report.query.filter_by(generated_by=user.id).delete(synchronize_session=False)
    Schedule.query.filter_by(requested_by=user.id).delete(synchronize_session=False)
    EventRequest.query.filter_by(requester_id=user.id).delete(synchronize_session=False)
    BlotterCase.query.filter_by(complainant_id=user.id).delete(synchronize_session=False)
    PermitApplication.query.filter_by(applicant_id=user.id).delete(synchronize_session=False)
    PasswordResetToken.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    ActivityLog.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    User.query.filter_by(id=user.id).delete(synchronize_session=False)

with app.app_context():
    upgrade_legacy_schema()
    for existing in User.query.filter(User.username.like(PREFIX+"%")):
        cleanup_user(existing)
    if len(sys.argv)>1 and sys.argv[1]=="cleanup":
        db.session.commit(); print("BROWSER_QA_ACCOUNTS_REMOVED")
    else:
        for role in ("resident","staff","admin"):
            user=User(username=PREFIX+role,email=f"{PREFIX}{role}@local.invalid",full_name=f"QA {role.title()}",role=role); user.set_password("BrowserQA123"); db.session.add(user)
        db.session.commit(); print("BROWSER_QA_ACCOUNTS_READY")
