import hashlib, secrets, sqlite3
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def configure_sqlite_connection(dbapi_connection, _connection_record):
    """Apply integrity and lock-safety settings to every SQLite connection."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def utcnow(): return datetime.utcnow()
def make_reference(prefix): return f"{prefix}-{utcnow().year}-{secrets.randbelow(1000000):06d}"

class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

class User(db.Model, TimestampMixin):
    __tablename__ = "users"
    id=db.Column(db.Integer,primary_key=True); username=db.Column(db.String(80),unique=True,nullable=False,index=True)
    password_hash=db.Column(db.String(255),nullable=False); role=db.Column(db.String(20),nullable=False,default="resident",index=True)
    full_name=db.Column(db.String(150),nullable=False); contact_number=db.Column(db.String(20)); email=db.Column(db.String(150),unique=True,index=True)
    address=db.Column(db.String(255)); is_active=db.Column(db.Boolean,nullable=False,default=True)
    def set_password(self,raw): self.password_hash=generate_password_hash(raw)
    def check_password(self,raw): return check_password_hash(self.password_hash,raw)

class PermitType(db.Model, TimestampMixin):
    __tablename__="permit_types"
    id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(120),nullable=False,unique=True); description=db.Column(db.Text)
    requirements=db.Column(db.Text); fee=db.Column(db.Numeric(10,2),default=0); validity_days=db.Column(db.Integer,default=365); is_active=db.Column(db.Boolean,default=True)

class EventCategory(db.Model, TimestampMixin):
    __tablename__="event_categories"
    id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(120),unique=True,nullable=False); is_active=db.Column(db.Boolean,default=True)

class PermitApplication(db.Model, TimestampMixin):
    __tablename__="permit_applications"
    id=db.Column(db.Integer,primary_key=True); reference_no=db.Column(db.String(40),unique=True,nullable=False,default=lambda:make_reference("PRM"))
    applicant_id=db.Column(db.Integer,db.ForeignKey("users.id"),nullable=False,index=True); permit_type_id=db.Column(db.Integer,db.ForeignKey("permit_types.id"),nullable=False)
    purpose=db.Column(db.Text,nullable=False); status=db.Column(db.String(30),nullable=False,default="Pending",index=True); application_date=db.Column(db.DateTime,default=utcnow)
    appointment_date=db.Column(db.DateTime); decision_date=db.Column(db.DateTime); decided_by=db.Column(db.Integer,db.ForeignKey("users.id")); reviewed_by=db.Column(db.Integer,db.ForeignKey("users.id"))
    remarks=db.Column(db.Text); permit_file_path=db.Column(db.String(255)); attachment_path=db.Column(db.String(255))
    applicant=db.relationship("User",foreign_keys=[applicant_id],backref="permit_applications"); permit_type=db.relationship("PermitType")

class EventRequest(db.Model, TimestampMixin):
    __tablename__="event_requests"
    id=db.Column(db.Integer,primary_key=True); reference_no=db.Column(db.String(40),unique=True,nullable=False,default=lambda:make_reference("EVT"))
    requester_id=db.Column(db.Integer,db.ForeignKey("users.id"),nullable=False,index=True); event_name=db.Column(db.String(180),nullable=False); category_id=db.Column(db.Integer,db.ForeignKey("event_categories.id"))
    event_type=db.Column(db.String(120),nullable=False); description=db.Column(db.Text,nullable=False); proposed_date=db.Column(db.Date,nullable=False); proposed_start_time=db.Column(db.Time); proposed_end_time=db.Column(db.Time)
    proposed_location=db.Column(db.String(255),nullable=False); expected_attendees=db.Column(db.Integer,nullable=False); supporting_document_path=db.Column(db.String(255)); status=db.Column(db.String(30),nullable=False,default="Pending",index=True)
    reviewed_by=db.Column(db.Integer,db.ForeignKey("users.id")); decision_date=db.Column(db.DateTime); remarks=db.Column(db.Text)
    requester=db.relationship("User",foreign_keys=[requester_id],backref="event_requests"); category=db.relationship("EventCategory")

class BlotterCase(db.Model, TimestampMixin):
    __tablename__="blotter_cases"
    id=db.Column(db.Integer,primary_key=True); case_no=db.Column(db.String(40),unique=True,nullable=False,default=lambda:make_reference("BLT")); complainant_id=db.Column(db.Integer,db.ForeignKey("users.id"),nullable=False,index=True)
    respondent_name=db.Column(db.String(150),nullable=False); respondent_address=db.Column(db.String(255)); incident_type=db.Column(db.String(120),nullable=False); incident_date=db.Column(db.DateTime,nullable=False)
    incident_location=db.Column(db.String(255),nullable=False); narrative=db.Column(db.Text,nullable=False); supporting_document_path=db.Column(db.String(255)); status=db.Column(db.String(30),nullable=False,default="Filed",index=True)
    filed_date=db.Column(db.DateTime,default=utcnow); assigned_officer=db.Column(db.String(150)); resolution=db.Column(db.Text); hearing_date=db.Column(db.DateTime); updated_by=db.Column(db.Integer,db.ForeignKey("users.id"))
    complainant=db.relationship("User",foreign_keys=[complainant_id],backref="blotter_cases")

class Schedule(db.Model, TimestampMixin):
    __tablename__="schedules"
    id=db.Column(db.Integer,primary_key=True); reference_no=db.Column(db.String(40),unique=True,nullable=False,default=lambda:make_reference("SCH")); related_type=db.Column(db.String(20),nullable=False,index=True); related_id=db.Column(db.Integer,nullable=False,index=True)
    requested_by=db.Column(db.Integer,db.ForeignKey("users.id"),nullable=False); scheduled_datetime=db.Column(db.DateTime,nullable=False,index=True); purpose=db.Column(db.String(255),nullable=False); note=db.Column(db.Text); status=db.Column(db.String(30),nullable=False,default="Requested",index=True); confirmed_by=db.Column(db.Integer,db.ForeignKey("users.id")); requester=db.relationship("User",foreign_keys=[requested_by])

class Notification(db.Model):
    __tablename__="notifications"
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey("users.id"),index=True); recipient_user_id=db.Column(db.Integer,db.ForeignKey("users.id")); recipient_contact=db.Column(db.String(150))
    title=db.Column(db.String(180),nullable=False,default="System notification"); message=db.Column(db.Text,nullable=False); channel=db.Column(db.String(20),nullable=False,default="in_app",index=True); status=db.Column(db.String(20),nullable=False,default="sent")
    related_type=db.Column(db.String(30)); related_id=db.Column(db.Integer); sent_at=db.Column(db.DateTime); read_at=db.Column(db.DateTime); created_at=db.Column(db.DateTime,nullable=False,default=utcnow); user=db.relationship("User",foreign_keys=[user_id])

class ActivityLog(db.Model):
    __tablename__="activity_logs"
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey("users.id"),index=True); actor_role=db.Column(db.String(20)); action=db.Column(db.String(120),nullable=False,index=True); resource=db.Column(db.String(50),nullable=False,default="system",index=True)
    target_reference=db.Column(db.String(80)); details=db.Column(db.Text); ip_address=db.Column(db.String(50)); user_agent=db.Column(db.String(255)); timestamp=db.Column(db.DateTime,nullable=False,default=utcnow,index=True); user=db.relationship("User")

class Report(db.Model):
    __tablename__="reports"
    id=db.Column(db.Integer,primary_key=True); generated_by=db.Column(db.Integer,db.ForeignKey("users.id"),nullable=False); report_type=db.Column(db.String(50),nullable=False); format=db.Column(db.String(10),nullable=False); filters_json=db.Column(db.Text); created_at=db.Column(db.DateTime,nullable=False,default=utcnow)

class PasswordResetToken(db.Model):
    __tablename__="password_reset_tokens"
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey("users.id"),nullable=False,index=True); token_hash=db.Column(db.String(64),unique=True,nullable=False,index=True); expires_at=db.Column(db.DateTime,nullable=False); used_at=db.Column(db.DateTime); created_at=db.Column(db.DateTime,nullable=False,default=utcnow); user=db.relationship("User")
    @classmethod
    def issue(cls,user,lifetime_minutes=30):
        cls.query.filter_by(user_id=user.id,used_at=None).update({"used_at":utcnow()}); raw=secrets.token_urlsafe(32); record=cls(user_id=user.id,token_hash=hashlib.sha256(raw.encode()).hexdigest(),expires_at=utcnow()+timedelta(minutes=lifetime_minutes)); db.session.add(record); return raw,record
    @classmethod
    def valid_for(cls,raw): return cls.query.filter(cls.token_hash==hashlib.sha256(raw.encode()).hexdigest(),cls.used_at.is_(None),cls.expires_at>utcnow()).first()

class SystemSetting(db.Model, TimestampMixin):
    __tablename__="system_settings"
    id=db.Column(db.Integer,primary_key=True); key=db.Column(db.String(100),unique=True,nullable=False); value=db.Column(db.Text)

class Announcement(db.Model, TimestampMixin):
    __tablename__="announcements"
    id=db.Column(db.Integer,primary_key=True)
    title=db.Column(db.String(180),nullable=False)
    body=db.Column(db.Text,nullable=False)
    is_published=db.Column(db.Boolean,nullable=False,default=True,index=True)
    published_at=db.Column(db.DateTime,nullable=False,default=utcnow,index=True)
    expires_at=db.Column(db.DateTime)
    created_by=db.Column(db.Integer,db.ForeignKey("users.id"),nullable=False,index=True)
    author=db.relationship("User",foreign_keys=[created_by])

def log_activity(user_id,action,details=None,ip_address=None,resource="system",target_reference=None,actor_role=None,user_agent=None,commit=True):
    if actor_role is None and user_id:
        user=db.session.get(User,user_id); actor_role=user.role if user else None
    entry=ActivityLog(user_id=user_id,actor_role=actor_role,action=action,resource=resource,target_reference=target_reference,details=details,ip_address=ip_address,user_agent=(user_agent or "")[:255]); db.session.add(entry)
    if commit: db.session.commit()
    return entry
