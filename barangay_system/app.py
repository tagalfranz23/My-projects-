import csv, io, json, os, re, secrets
from datetime import datetime, date
from functools import wraps
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for, send_file
from sqlalchemy import func, or_, text
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from config import Config
from models import (db, User, PermitType, PermitApplication, EventCategory, EventRequest,
 BlotterCase, Schedule, Notification, ActivityLog, Report, PasswordResetToken, SystemSetting,
 Announcement, log_activity)
from permissions import can, permission_required, navigation_for
from workflows import allowed_transitions, validate_transition
from services.notify import notify_status, create_notification
from services.pdfgen import generate_submission_receipt, generate_permit_pdf
from migration import upgrade_legacy_schema

app=Flask(__name__); app.config.from_object(Config)
for durable_folder in (app.config["DATA_DIR"], app.config["UPLOAD_FOLDER"], app.config["PERMIT_FOLDER"]):
    os.makedirs(durable_folder,exist_ok=True)
app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=1)
db.init_app(app)
app.config.setdefault("MAX_CONTENT_LENGTH",8*1024*1024); app.config.setdefault("RESET_TOKEN_MINUTES",30)
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Lax")
if os.environ.get("RENDER") and app.config["SECRET_KEY"]=="development-only-change-me":
    raise RuntimeError("Render deployment requires a strong SECRET_KEY environment value.")
OFFICIAL_NAME="Development of an Integrated Barangay Permit, Event Approval, Blotter Management System with Notification and Scheduling Features for Barangay Minante 1, Cauayan City"
SHORT_NAME="Barangay Minante 1 Integrated Management System"; LOCATION="Barangay Minante 1, Cauayan City, Isabela"
ALLOWED_UPLOADS={"pdf","png","jpg","jpeg"}

def now(): return datetime.utcnow()
def actor(): return getattr(g,"user",None)
def audit(action,resource="system",ref=None,details=None):
    return log_activity(actor().id if actor() else None,action,details,request.remote_addr,resource,ref,
                        user_agent=request.headers.get("User-Agent"),commit=False)
def password_valid(value): return len(value)>=10 and re.search(r"[A-Za-z]",value) and re.search(r"\d",value)
def save_upload(field):
    f=request.files.get(field)
    if not f or not f.filename: return None
    ext=f.filename.rsplit(".",1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_UPLOADS: raise ValueError("Only PDF, PNG, and JPEG files are allowed.")
    folder=app.config["UPLOAD_FOLDER"]; os.makedirs(folder,exist_ok=True)
    name=f"{secrets.token_hex(16)}_{secure_filename(f.filename)}"; f.save(os.path.join(folder,name)); return name

def receipt_record(kind,record_id):
    mapping={"permit":(PermitApplication,"applicant_id"),"event":(EventRequest,"requester_id"),"blotter":(BlotterCase,"complainant_id")}
    if kind not in mapping: abort(404)
    model,owner_field=mapping[kind]; item=db.session.get(model,record_id)
    if not item: abort(404)
    owner_id=getattr(item,owner_field)
    if actor().role=="resident" and owner_id!=actor().id: abort(403)
    if actor().role not in {"resident","staff","admin"}: abort(403)
    return item

def receipt_processing_time(kind):
    setting=SystemSetting.query.filter_by(key=f"receipt_processing_{kind}").first()
    return setting.value if setting and setting.value else {"permit":"1–3 business days","event":"3–5 business days","blotter":"1–3 business days"}[kind]

@app.before_request
def load_user_and_csrf():
    g.user=db.session.get(User,session.get("user_id")) if session.get("user_id") else None
    if "csrf_token" not in session: session["csrf_token"]=secrets.token_urlsafe(32)
    if request.method in {"POST","PUT","PATCH","DELETE"}:
        supplied=request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not supplied or not secrets.compare_digest(supplied,session["csrf_token"]): abort(400,"Invalid or missing CSRF token.")

def login_required(view):
    @wraps(view)
    def wrapped(*a,**kw):
        if not actor(): flash("Please log in to continue.","warning"); return redirect(url_for("login",next=request.path))
        if not actor().is_active: session.clear(); abort(403)
        return view(*a,**kw)
    return wrapped

@app.context_processor
def globals_context():
    unread=Notification.query.filter_by(user_id=actor().id,channel="in_app",read_at=None).count() if actor() else 0
    return dict(current_user=actor(),official_name=OFFICIAL_NAME,short_name=SHORT_NAME,location=LOCATION,
                nav_items=navigation_for(actor().role) if actor() else [],csrf_token=session.get("csrf_token"),unread_count=unread,can=can)

@app.route("/")
def index(): return redirect(url_for("dashboard") if actor() else url_for("login"))

@app.route("/favicon.ico")
def favicon(): return "",204

@app.get("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
        return {"status":"ok","database":"sqlite"},200
    except Exception:
        db.session.rollback(); app.logger.exception("SQLite health check failed")
        return {"status":"unhealthy"},503

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        value=request.form.get("username","").strip(); user=User.query.filter(or_(User.username==value,User.email==value)).first()
        if user and user.is_active and user.check_password(request.form.get("password","")):
            remember=bool(request.form.get("remember")); session.clear(); session.permanent=remember; session["user_id"]=user.id; session["csrf_token"]=secrets.token_urlsafe(32); g.user=user
            audit("LOGIN","authentication",str(user.id)); db.session.commit(); return redirect(url_for("dashboard"))
        flash("Invalid username or password.","danger")
    return render_template("login.html")

@app.post("/logout")
@login_required
def logout(): audit("LOGOUT","authentication",str(actor().id)); db.session.commit(); session.clear(); return redirect(url_for("login"))

@app.route("/register",methods=["GET","POST"])
def register():
    if request.method=="POST":
        username=request.form.get("username","").strip(); email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        if not username or not email or "@" not in email or not request.form.get("full_name","").strip(): flash("Complete all required fields with a valid email.","danger")
        elif User.query.filter(or_(User.username==username,User.email==email)).first(): flash("Username or email is already registered.","danger")
        elif not password_valid(password): flash("Password must have at least 10 characters, including a letter and number.","danger")
        else:
            user=User(username=username,email=email,role="resident",full_name=request.form["full_name"].strip(),contact_number=request.form.get("contact_number","").strip(),address=request.form.get("address","").strip()); user.set_password(password); db.session.add(user); db.session.flush()
            log_activity(user.id,"ACCOUNT_CREATED","Resident self-registration",request.remote_addr,"users",str(user.id),actor_role="resident",commit=False); db.session.commit(); flash("Account created. You may now log in.","success"); return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/forgot-password",methods=["GET","POST"])
def forgot_password():
    reset_link=None
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); user=User.query.filter(func.lower(User.email)==email,User.is_active.is_(True)).first()
        if user:
            raw,_=PasswordResetToken.issue(user,app.config["RESET_TOKEN_MINUTES"]); reset_link=url_for("reset_password",token=raw,_external=True)
            # The raw token is handed only to delivery; it is never persisted.
            create_notification(user.id,"Password reset instructions",f"Secure password reset instructions were issued and expire in {app.config['RESET_TOKEN_MINUTES']} minutes.","email",commit=False)
            log_activity(user.id,"PASSWORD_RESET_REQUESTED","Recovery email requested",request.remote_addr,"authentication",str(user.id),commit=False); db.session.commit()
        flash("If an account is associated with that email address, password reset instructions will be sent.","info")
    return render_template("forgot_password.html",development_reset_link=reset_link if app.debug else None)

@app.route("/reset-password/<token>",methods=["GET","POST"])
def reset_password(token):
    record=PasswordResetToken.valid_for(token)
    if not record: flash("This password reset link is invalid or has expired.","danger"); return redirect(url_for("forgot_password"))
    if request.method=="POST":
        password=request.form.get("password","")
        if password!=request.form.get("confirm_password",""): flash("Passwords do not match.","danger")
        elif not password_valid(password): flash("Password must have at least 10 characters, including a letter and number.","danger")
        else:
            record.user.set_password(password); record.used_at=now(); PasswordResetToken.query.filter(PasswordResetToken.user_id==record.user_id,PasswordResetToken.used_at.is_(None)).update({"used_at":now()})
            log_activity(record.user_id,"PASSWORD_RESET_COMPLETED","Password securely changed",request.remote_addr,"authentication",str(record.user_id),commit=False); db.session.commit(); flash("Password reset complete. Please log in.","success"); return redirect(url_for("login"))
    return render_template("reset_password.html")

@app.route("/profile",methods=["GET","POST"])
@login_required
@permission_required("profile","view")
def profile():
    u=actor()
    if request.method=="POST":
        action=request.form.get("action","profile")
        if action=="password":
            current=request.form.get("current_password",""); password=request.form.get("password","")
            if not u.check_password(current): flash("Current password is incorrect.","danger")
            elif password!=request.form.get("confirm_password",""): flash("New passwords do not match.","danger")
            elif not password_valid(password): flash("Password must have at least 10 characters, including a letter and number.","danger")
            else: u.set_password(password); audit("PASSWORD_CHANGED","profile",str(u.id)); db.session.commit(); flash("Password changed securely.","success")
        else:
            full_name=request.form.get("full_name","").strip(); email=request.form.get("email","").strip().lower()
            if not full_name or "@" not in email: flash("A full name and valid email are required.","danger")
            elif User.query.filter(User.email==email,User.id!=u.id).first(): flash("That email is already registered.","danger")
            else:
                u.full_name=full_name; u.email=email; u.contact_number=request.form.get("contact_number","").strip(); u.address=request.form.get("address","").strip()
                audit("PROFILE_UPDATED","profile",str(u.id)); db.session.commit(); flash("Profile updated.","success")
    return render_template("profile.html")

@app.route("/announcements",methods=["GET","POST"])
@login_required
@permission_required("announcement","view")
def announcements():
    if request.method=="POST":
        if actor().role!="admin": abort(403)
        title=request.form.get("title","").strip(); body=request.form.get("body","").strip()
        if not title or not body: flash("Announcement title and message are required.","danger")
        else:
            item=Announcement(title=title,body=body,created_by=actor().id); db.session.add(item); db.session.flush(); audit("ANNOUNCEMENT_CREATED","announcement",str(item.id),title); db.session.commit(); flash("Announcement published.","success")
    query=Announcement.query
    if actor().role!="admin": query=query.filter(Announcement.is_published.is_(True),or_(Announcement.expires_at.is_(None),Announcement.expires_at>now()))
    return render_template("announcements.html",records=query.order_by(Announcement.published_at.desc()).all())

@app.post("/announcements/<int:record_id>/delete")
@login_required
@permission_required("announcement","manage")
def announcement_delete(record_id):
    item=Announcement.query.get_or_404(record_id); ref=str(item.id); title=item.title; db.session.delete(item); audit("ANNOUNCEMENT_DELETED","announcement",ref,title); db.session.commit(); return redirect(url_for("announcements"))

@app.route("/dashboard")
@login_required
def dashboard():
    u=actor(); own=u.role=="resident"
    pq=PermitApplication.query.filter_by(applicant_id=u.id) if own else PermitApplication.query
    eq=EventRequest.query.filter_by(requester_id=u.id) if own else EventRequest.query
    bq=BlotterCase.query.filter_by(complainant_id=u.id) if own else BlotterCase.query
    sq=Schedule.query.filter_by(requested_by=u.id) if own else Schedule.query
    if u.role=="admin":
        stats={"registered_residents":User.query.filter_by(role="resident",is_active=True).count(),"permit_applications":pq.count(),"event_requests":eq.count(),"open_blotter_cases":bq.filter(~BlotterCase.status.in_(["Resolved","Closed"])).count(),"today_schedules":sq.filter(func.date(Schedule.scheduled_datetime)==date.today()).count(),"pending_final_decisions":pq.filter(PermitApplication.status=="Endorsed to Admin").count()+eq.filter(EventRequest.status=="Endorsed to Admin").count()}
    elif u.role=="staff":
        stats={"requests_awaiting_review":pq.filter(PermitApplication.status=="Pending").count()+eq.filter(EventRequest.status=="Pending").count(),"under_review":pq.filter(PermitApplication.status=="Under Review").count()+eq.filter(EventRequest.status=="Under Review").count(),"new_blotter_reports":bq.filter(BlotterCase.status=="Filed").count(),"endorsed_to_admin":pq.filter(PermitApplication.status=="Endorsed to Admin").count()+eq.filter(EventRequest.status=="Endorsed to Admin").count(),"today_appointments":sq.filter(func.date(Schedule.scheduled_datetime)==date.today()).count()}
    else:
        active_statuses=["Pending","Under Review","Endorsed to Admin","Approved","Ready for Pickup"]
        stats={"active_applications":pq.filter(PermitApplication.status.in_(active_statuses)).count()+eq.filter(EventRequest.status.in_(active_statuses)).count(),"blotter_reports":bq.count(),"upcoming_appointments":sq.filter(Schedule.scheduled_datetime>=now(),Schedule.status.in_(["Requested","Confirmed","Rescheduled"])).count(),"unread_notifications":Notification.query.filter_by(user_id=u.id,channel="in_app",read_at=None).count()}
    return render_template("dashboard.html",stats=stats,permits=pq.order_by(PermitApplication.application_date.desc()).limit(6).all(),events=eq.order_by(EventRequest.created_at.desc()).limit(6).all(),schedules=sq.filter(Schedule.scheduled_datetime>=now()).order_by(Schedule.scheduled_datetime).limit(5).all(),recent_notifications=Notification.query.filter_by(user_id=u.id).order_by(Notification.created_at.desc()).limit(3).all())

@app.route("/status")
@login_required
@permission_required("status","view_own")
def transaction_status():
    return render_template("transaction_status.html",
        permits=PermitApplication.query.filter_by(applicant_id=actor().id).order_by(PermitApplication.application_date.desc()).all(),
        events=EventRequest.query.filter_by(requester_id=actor().id).order_by(EventRequest.created_at.desc()).all(),
        blotters=BlotterCase.query.filter_by(complainant_id=actor().id).order_by(BlotterCase.filed_date.desc()).all(),
        schedules=Schedule.query.filter_by(requested_by=actor().id).order_by(Schedule.scheduled_datetime.desc()).all())

def scoped(query,owner_column): return query.filter(owner_column==actor().id) if actor().role=="resident" else query
def ensure_ownership(record,owner_id):
    if actor().role=="resident" and owner_id!=actor().id: abort(403)

@app.route("/permits",methods=["GET","POST"])
@login_required
def permits():
    if request.method=="POST":
        if not can(actor().role,"permit","create"): abort(403)
        pt=db.session.get(PermitType,request.form.get("permit_type_id")); purpose=request.form.get("purpose","").strip()
        if not pt or not purpose: flash("Permit type and purpose are required.","danger")
        else:
            try: attachment=save_upload("attachment")
            except ValueError as e: flash(str(e),"danger"); attachment=None
            item=PermitApplication(applicant_id=actor().id,permit_type_id=pt.id,purpose=purpose,attachment_path=attachment); db.session.add(item); db.session.flush(); audit("PERMIT_SUBMITTED","permit",item.reference_no); notify_status(actor().id,"Permit application received",f"{item.reference_no} is Pending. Your acknowledgment receipt is available in your account.","permit",item.id); db.session.commit(); return redirect(url_for("submission_success",kind="permit",record_id=item.id))
    return render_template("records.html",kind="permit",records=scoped(PermitApplication.query,PermitApplication.applicant_id).order_by(PermitApplication.application_date.desc()).all(),types=PermitType.query.filter_by(is_active=True).all())

@app.route("/permits/<int:record_id>",methods=["GET","POST"])
@login_required
def permit_detail(record_id):
    item=PermitApplication.query.get_or_404(record_id); ensure_ownership(item,item.applicant_id)
    if request.method=="POST": update_status(item,"permit",item.applicant_id,item.reference_no)
    return render_template("record_detail.html",kind="permit",record=item,transitions=allowed_transitions("permit",actor().role,item.status))

@app.route("/events",methods=["GET","POST"])
@login_required
def events():
    if request.method=="POST":
        if not can(actor().role,"event","create"): abort(403)
        try:
            attendees=int(request.form.get("expected_attendees",0)); event_date=datetime.strptime(request.form.get("proposed_date",""),"%Y-%m-%d").date()
            if attendees<1: raise ValueError
            item=EventRequest(requester_id=actor().id,event_name=request.form.get("event_name","").strip(),event_type=request.form.get("event_type","").strip(),description=request.form.get("description","").strip(),proposed_date=event_date,proposed_location=request.form.get("proposed_location","").strip(),expected_attendees=attendees,supporting_document_path=save_upload("attachment"))
            if not all((item.event_name,item.event_type,item.description,item.proposed_location)): raise ValueError
            db.session.add(item); db.session.flush(); audit("EVENT_SUBMITTED","event",item.reference_no); notify_status(actor().id,"Event request received",f"{item.reference_no} is Pending. Your acknowledgment receipt is available in your account.","event",item.id); db.session.commit(); return redirect(url_for("submission_success",kind="event",record_id=item.id))
        except (ValueError,TypeError): db.session.rollback(); flash("Provide complete, valid event details and at least one attendee.","danger")
    return render_template("records.html",kind="event",records=scoped(EventRequest.query,EventRequest.requester_id).order_by(EventRequest.created_at.desc()).all())

@app.route("/events/<int:record_id>",methods=["GET","POST"])
@login_required
def event_detail(record_id):
    item=EventRequest.query.get_or_404(record_id); ensure_ownership(item,item.requester_id)
    if request.method=="POST": update_status(item,"event",item.requester_id,item.reference_no)
    return render_template("record_detail.html",kind="event",record=item,transitions=allowed_transitions("event",actor().role,item.status))

@app.route("/blotters",methods=["GET","POST"])
@login_required
def blotters():
    if request.method=="POST":
        if not can(actor().role,"blotter","create"): abort(403)
        try: incident=datetime.strptime(request.form.get("incident_date",""),"%Y-%m-%dT%H:%M")
        except ValueError: flash("A valid incident date and time is required.","danger"); incident=None
        if incident:
            item=BlotterCase(complainant_id=actor().id,respondent_name=request.form.get("respondent_name","").strip(),incident_type=request.form.get("incident_type","").strip(),incident_date=incident,incident_location=request.form.get("incident_location","").strip(),narrative=request.form.get("narrative","").strip(),supporting_document_path=save_upload("attachment"))
            if not all((item.respondent_name,item.incident_type,item.incident_location,item.narrative)): flash("Complete all required complaint fields.","danger")
            else: db.session.add(item); db.session.flush(); audit("BLOTTER_FILED","blotter",item.case_no); notify_status(actor().id,"Blotter report received",f"{item.case_no} was filed. Your acknowledgment receipt is available in your account.","blotter",item.id); db.session.commit(); return redirect(url_for("submission_success",kind="blotter",record_id=item.id))
    return render_template("records.html",kind="blotter",records=scoped(BlotterCase.query,BlotterCase.complainant_id).order_by(BlotterCase.filed_date.desc()).all())

@app.route("/blotters/<int:record_id>",methods=["GET","POST"])
@login_required
def blotter_detail(record_id):
    item=BlotterCase.query.get_or_404(record_id); ensure_ownership(item,item.complainant_id)
    if request.method=="POST": update_status(item,"blotter",item.complainant_id,item.case_no)
    return render_template("record_detail.html",kind="blotter",record=item,transitions=allowed_transitions("blotter",actor().role,item.status))

@app.route("/submissions/<kind>/<int:record_id>/success")
@login_required
def submission_success(kind,record_id):
    item=receipt_record(kind,record_id)
    reference=item.case_no if kind=="blotter" else item.reference_no
    detail_endpoint={"permit":"permit_detail","event":"event_detail","blotter":"blotter_detail"}[kind]
    return render_template("submission_success.html",kind=kind,record=item,reference=reference,detail_endpoint=detail_endpoint)

@app.route("/receipts/<kind>/<int:record_id>.pdf")
@login_required
def submission_receipt(kind,record_id):
    item=receipt_record(kind,record_id); reference=item.case_no if kind=="blotter" else item.reference_no
    try:
        pdf=generate_submission_receipt(item,kind,receipt_processing_time(kind),"Barangay Minante 1","Cauayan City, Isabela")
        audit("SUBMISSION_RECEIPT_GENERATED",kind,reference,"Authorized receipt download"); db.session.commit()
        return send_file(pdf,mimetype="application/pdf",as_attachment=True,download_name=f"acknowledgment-{reference}.pdf")
    except Exception:
        db.session.rollback(); app.logger.exception("Receipt generation failed for %s %s",kind,reference)
        log_activity(actor().id,"SUBMISSION_RECEIPT_GENERATION_FAILED","PDF generation failed",request.remote_addr,kind,reference,user_agent=request.headers.get("User-Agent"))
        abort(500)

@app.route("/permits/<int:record_id>/approved-document")
@login_required
def approved_permit_document(record_id):
    item=PermitApplication.query.get_or_404(record_id); ensure_ownership(item,item.applicant_id)
    if item.status not in {"Approved","Ready for Pickup","Completed"}: abort(403)
    path=os.path.join(app.instance_path,f"approved-{item.reference_no}-{secrets.token_hex(4)}.pdf")
    try:
        generate_permit_pdf(item,item.applicant,item.permit_type,"Barangay Minante 1","Cauayan City, Isabela",path)
        with open(path,"rb") as source: payload=io.BytesIO(source.read())
        audit("APPROVED_PERMIT_DOCUMENT_DOWNLOADED","permit",item.reference_no); db.session.commit()
        return send_file(payload,mimetype="application/pdf",as_attachment=True,download_name=f"approved-{item.reference_no}.pdf")
    finally:
        if os.path.exists(path): os.remove(path)

def update_status(item,kind,recipient_id,reference):
    if actor().role=="resident" or not can(actor().role,kind,"review") and actor().role!="admin": abort(403)
    target=request.form.get("status"); old=item.status
    if not validate_transition(kind,actor().role,old,target): abort(400,"Invalid status transition.")
    item.status=target; item.remarks=request.form.get("remarks","").strip() if hasattr(item,"remarks") else None
    if hasattr(item,"reviewed_by"): item.reviewed_by=actor().id
    if target in {"Approved","Rejected"}: item.decision_date=now()
    audit(f"{kind.upper()}_STATUS_CHANGED",kind,reference,f"{old} -> {target}"); notify_status(recipient_id,f"{kind.title()} status updated",f"{reference}: {target}",kind,item.id); db.session.commit(); flash("Status updated and notifications recorded.","success")

@app.route("/schedules",methods=["GET","POST"])
@login_required
def schedules():
    if request.method=="POST":
        if not can(actor().role,"schedule","create"): abort(403)
        rt=request.form.get("related_type"); rid=request.form.get("related_id",type=int)
        if rt not in {"permit","event","blotter"} or not rid: abort(400)
        try: dt=datetime.strptime(request.form.get("scheduled_datetime",""),"%Y-%m-%dT%H:%M")
        except ValueError: flash("Valid schedule date/time required.","danger"); dt=None
        if dt:
            owner=related_owner(rt,rid); ensure_ownership(None,owner)
            item=Schedule(related_type=rt,related_id=rid,requested_by=owner,scheduled_datetime=dt,purpose=request.form.get("purpose","").strip() or "Barangay transaction"); db.session.add(item); db.session.flush(); audit("SCHEDULE_CREATED","schedule",item.reference_no); notify_status(owner,"Schedule requested",f"{item.reference_no}: {dt:%b %d, %Y %I:%M %p}","schedule",item.id); db.session.commit(); return redirect(url_for("schedules"))
    q=Schedule.query.filter_by(requested_by=actor().id) if actor().role=="resident" else Schedule.query
    return render_template("schedules.html",records=q.order_by(Schedule.scheduled_datetime).all())

def related_owner(kind,rid):
    model,col={"permit":(PermitApplication,"applicant_id"),"event":(EventRequest,"requester_id"),"blotter":(BlotterCase,"complainant_id")}[kind]; obj=db.session.get(model,rid)
    if not obj: abort(400,"Related record not found.")
    return getattr(obj,col)

@app.post("/schedules/<int:record_id>/status")
@login_required
def schedule_status(record_id):
    item=Schedule.query.get_or_404(record_id); ensure_ownership(item,item.requested_by)
    if actor().role=="resident": abort(403)
    target=request.form.get("status"); old=item.status
    if not validate_transition("schedule",actor().role,old,target): abort(400,"Invalid schedule transition.")
    item.status=target; item.confirmed_by=actor().id; audit("SCHEDULE_STATUS_CHANGED","schedule",item.reference_no,f"{old} -> {target}"); notify_status(item.requested_by,"Schedule updated",f"{item.reference_no}: {target}","schedule",item.id); db.session.commit(); return redirect(url_for("schedules"))

@app.route("/notifications")
@login_required
def notifications(): return render_template("notifications.html",records=Notification.query.filter_by(user_id=actor().id).order_by(Notification.created_at.desc()).all(),all_users=User.query.filter_by(is_active=True).order_by(User.full_name).all() if actor().role=="admin" else [])
@app.post("/notifications/send")
@login_required
@permission_required("notification","manage")
def notification_send():
    recipient=db.session.get(User,request.form.get("user_id",type=int)); channel=request.form.get("channel","in_app")
    title=request.form.get("title","").strip(); message=request.form.get("message","").strip()
    if not recipient or not title or not message or channel not in {"sms","email","in_app"}: abort(400,"Valid recipient, channel, title, and message are required.")
    item=create_notification(recipient.id,title,message,channel,commit=False); audit("NOTIFICATION_SENT","notification",str(item.id),f"recipient={recipient.id}; channel={channel}"); db.session.commit(); flash("Notification recorded and sent through the configured channel.","success"); return redirect(url_for("notifications"))
@app.post("/notifications/<int:record_id>/read")
@login_required
def notification_read(record_id):
    item=Notification.query.filter_by(id=record_id,user_id=actor().id).first_or_404(); item.read_at=now(); db.session.commit(); return redirect(url_for("notifications"))
@app.post("/notifications/read-all")
@login_required
def notifications_read_all(): Notification.query.filter_by(user_id=actor().id,channel="in_app",read_at=None).update({"read_at":now()}); db.session.commit(); return redirect(url_for("notifications"))

@app.route("/users",methods=["GET","POST"])
@login_required
@permission_required("users","view")
def users():
    if request.method=="POST":
        if actor().role!="admin": abort(403)
        role=request.form.get("role"); password=request.form.get("password","")
        if role not in {"resident","staff","admin"} or not password_valid(password): flash("Select a valid role and strong password.","danger")
        else:
            item=User(username=request.form.get("username","").strip(),email=request.form.get("email","").strip().lower(),full_name=request.form.get("full_name","").strip(),role=role); item.set_password(password); db.session.add(item); db.session.flush(); audit("USER_CREATED","users",str(item.id),f"role={role}"); db.session.commit()
    return render_template("users.html",records=User.query.order_by(User.created_at.desc()).all())

@app.post("/users/<int:record_id>/role")
@login_required
@permission_required("users","manage")
def user_role(record_id):
    item=User.query.get_or_404(record_id); role=request.form.get("role")
    if role not in {"resident","staff","admin"}: abort(400,"Invalid role.")
    if item.id==actor().id and role!="admin": abort(400,"You cannot remove your own Administrator role.")
    old=item.role; item.role=role; audit("USER_ROLE_CHANGED","users",str(item.id),f"{old} -> {role}"); db.session.commit(); return redirect(url_for("users"))

@app.post("/users/<int:record_id>/status")
@login_required
@permission_required("users","manage")
def user_account_status(record_id):
    item=User.query.get_or_404(record_id)
    if item.id==actor().id: abort(400,"You cannot deactivate your own account.")
    item.is_active=not item.is_active; audit("USER_STATUS_CHANGED","users",str(item.id),"active" if item.is_active else "inactive"); db.session.commit(); return redirect(url_for("users"))

@app.route("/reports")
@login_required
@permission_required("reports","view")
def reports(): return render_template("reports.html",metrics=report_metrics())
def report_metrics():
    return {"permits":dict(db.session.query(PermitApplication.status,func.count()).group_by(PermitApplication.status).all()),"events":dict(db.session.query(EventRequest.status,func.count()).group_by(EventRequest.status).all()),"blotters":dict(db.session.query(BlotterCase.status,func.count()).group_by(BlotterCase.status).all()),"schedules":dict(db.session.query(Schedule.status,func.count()).group_by(Schedule.status).all()),"notifications":dict(db.session.query(Notification.channel,func.count()).group_by(Notification.channel).all())}
@app.post("/reports/export/<fmt>")
@login_required
@permission_required("reports","export")
def report_export(fmt):
    if fmt not in {"csv","pdf"}: abort(404)
    metrics=report_metrics(); report=Report(generated_by=actor().id,report_type="system_monitoring",format=fmt,filters_json=json.dumps(request.args)); db.session.add(report); audit("REPORT_EXPORTED","reports",None,fmt); db.session.commit()
    if fmt=="csv":
        output=io.StringIO(); writer=csv.writer(output); writer.writerow([OFFICIAL_NAME]); writer.writerow(["Module","Status/Channel","Count"])
        for module,values in metrics.items():
            for key,value in values.items(): writer.writerow([module,key,value])
        return app.response_class(output.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=barangay-report.csv"})
    from services.pdfgen import generate_report_pdf
    rows=[[module,key,str(value)] for module,values in metrics.items() for key,value in values.items()]
    path=os.path.join(app.instance_path,f"report-{report.id}.pdf")
    generate_report_pdf(OFFICIAL_NAME,["Module","Status / Channel","Count"],rows,path,subtitle=LOCATION)
    return send_file(path,as_attachment=True,download_name="barangay-report.pdf")

@app.route("/activity-logs")
@login_required
@permission_required("activity_logs","view_logs")
def activity_logs(): return render_template("activity_logs.html",records=ActivityLog.query.order_by(ActivityLog.timestamp.desc()).limit(500).all())

@app.route("/configuration",methods=["GET","POST"])
@login_required
@permission_required("configuration","manage")
def configuration():
    if request.method=="POST":
        kind=request.form.get("kind"); name=request.form.get("name","").strip()
        if kind=="permit" and name: db.session.add(PermitType(name=name,description=request.form.get("description",""),requirements=request.form.get("requirements",""),fee=request.form.get("fee") or 0))
        elif kind=="event" and name: db.session.add(EventCategory(name=name))
        elif kind=="receipt":
            for receipt_kind in ("permit","event","blotter"):
                value=request.form.get(f"processing_{receipt_kind}","").strip()
                if not value: continue
                setting=SystemSetting.query.filter_by(key=f"receipt_processing_{receipt_kind}").first()
                if setting: setting.value=value
                else: db.session.add(SystemSetting(key=f"receipt_processing_{receipt_kind}",value=value))
        else: abort(400)
        audit("CONFIGURATION_CHANGED","configuration",None,f"{kind}: {name}"); db.session.commit()
    return render_template("configuration.html",permit_types=PermitType.query.all(),event_categories=EventCategory.query.all(),receipt_processing=receipt_processing_time)

@app.errorhandler(400)
def bad_request(e): return render_template("error.html",code=400,message=getattr(e,"description","Invalid request.")),400
@app.errorhandler(401)
def unauthorized(e): return redirect(url_for("login"))
@app.errorhandler(403)
def forbidden(e): return render_template("error.html",code=403,message="You do not have permission to access this resource."),403
@app.errorhandler(404)
def missing(e): return render_template("error.html",code=404,message="The requested page was not found."),404
@app.errorhandler(500)
def server_error(e): db.session.rollback(); return render_template("error.html",code=500,message="An unexpected error occurred. Please try again."),500

@app.cli.command("init-db")
def init_db():
    """Create/upgrade schema without deleting the permanent administrator."""
    upgrade_legacy_schema(); print("Database initialized non-destructively; existing users preserved.")

@app.cli.command("create-admin")
def create_admin():
    """Idempotently establish a permanent admin from environment variables."""
    upgrade_legacy_schema()
    if User.query.filter_by(role="admin").first(): print("Permanent administrator already exists; preserved."); return
    username=os.environ.get("INITIAL_ADMIN_USERNAME"); email=os.environ.get("INITIAL_ADMIN_EMAIL"); password=os.environ.get("INITIAL_ADMIN_PASSWORD"); full_name=os.environ.get("INITIAL_ADMIN_NAME","System Administrator")
    if not username or not email or not password_valid(password or ""): raise RuntimeError("Set INITIAL_ADMIN_USERNAME, INITIAL_ADMIN_EMAIL, and a strong INITIAL_ADMIN_PASSWORD.")
    user=User(username=username,email=email.lower(),full_name=full_name,role="admin"); user.set_password(password); db.session.add(user); db.session.commit(); print("Permanent administrator created securely.")

if __name__=="__main__":
    with app.app_context(): upgrade_legacy_schema()
    app.run(debug=os.environ.get("FLASK_DEBUG")=="1")
