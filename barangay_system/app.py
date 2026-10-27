import json
import os 
import re
import secrets
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import Integer, cast, func, or_, text
from sqlalchemy.orm import contains_eager
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from migration import upgrade_legacy_schema
from models import (
    AcceptedIdType,
    ActivityLog,
    ApplicationRequirement,
    Announcement,
    BlotterCase,
    EventCategory,
    EventRequest,
    EventVenue,
    Notification,
    PasswordResetToken,
    PermitApplication,
    PermitSignatory,
    PermitType,
    QueueEntry,
    RequestAssignment,
    RequirementHistory,
    Report,
    ResidentProfile,
    ResidentFeedback,
    ResidentType,
    Schedule,
    SystemSetting,
    ServiceRequirement,
    User,
    db,
    log_activity,
)
from permissions import can, navigation_for, permission_required
from services.documentgen import (
    DOCX_MIMETYPE,
    generate_approved_permit_document,
    generate_walkin_claim_stub,
    generate_submission_acknowledgment,
)
from services.event_availability import (
    InvalidHold,
    ReservationConflict,
    accept_hold,
    create_hold,
    decline_hold,
    find_next_available,
    get_hold,
    release_event_reservation,
    reserve_event,
    reschedule_event,
)
from services.notify import create_notification
from workflows import allowed_transitions, validate_transition
from services.revision import (payment_status, local_display,
    local_datetime, CATEGORIES, active_announcements, refresh_announcements,
    requirement_submission)
from services.uploads import stage_upload
from services.resident_family import DISABILITY_TYPES, validate_family, save_family, form_family_rows


app = Flask(__name__)
app.config.from_object(Config)
os.environ.setdefault("MPLCONFIGDIR", os.path.join(app.config["DATA_DIR"], "chart-cache"))
for durable_folder in (
    app.config["DATA_DIR"],
    app.config["UPLOAD_FOLDER"],
    app.config["IDENTIFICATION_FOLDER"],
    app.config["PERMIT_FOLDER"],
    app.config["SIGNATURE_FOLDER"],
):
    os.makedirs(durable_folder, exist_ok=True)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
db.init_app(app)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config.setdefault("RESET_TOKEN_MINUTES", 30)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


@app.after_request
def protect_registration_details(response):
    if request.endpoint in {'register', 'user_review'}:
        response.headers['Cache-Control'] = 'private, no-store'
    return response


if os.environ.get("RENDER") and app.config["SECRET_KEY"] == "development-only-change-me":
    raise RuntimeError("Render deployment requires a strong SECRET_KEY environment value.")

OFFICIAL_NAME = (
    "Development of an Integrated Barangay Permit, Event Approval, Blotter "
    "Management System with Notification and Scheduling Features for Barangay "
    "Minante 1, Cauayan City"
)
SHORT_NAME = "Barangay Minante 1 Integrated Management System"
LOCATION = "Barangay Minante 1, Cauayan City, Isabela"
ALLOWED_UPLOADS = {"pdf", "png", "jpg", "jpeg"}
SEX_VALUES = {"Male", "Female"}
CIVIL_STATUS_VALUES = {"Single", "Married", "Widowed", "Separated", "Other"}
RESIDENCY_STATUS_VALUES = {
    "Permanent Resident",
    "Temporary Resident",
    "Former Resident / Moved Out",
}
VERIFICATION_STATUSES = {"Pending Verification", "Verified", "Rejected"}


def local_today():
    from services.revision import LOCAL_TIMEZONE
    return datetime.now(LOCAL_TIMEZONE).date()


def now():
    # Database columns are legacy naive UTC; keep their storage format while
    # obtaining the timestamp from an explicit timezone-aware UTC clock.
    return datetime.now(UTC).replace(tzinfo=None)


def actor():
    return getattr(g, "user", None)


def audit(action, resource="system", ref=None, details=None):
    return log_activity(
        actor().id if actor() else None,
        action,
        details,
        request.remote_addr,
        resource,
        ref,
        user_agent=request.headers.get("User-Agent"),
        commit=False,
    )


def password_valid(value):
    return bool(
        value
        and len(value) >= 10
        and re.search(r"[A-Za-z]", value)
        and re.search(r"\d", value)
    )


def email_valid(value):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value or ""))


def normalized_phone(value):
    return re.sub(r"[\s()-]", "", value or "")


def phone_valid(value):
    return bool(re.fullmatch(r"\+?[0-9]{10,15}", normalized_phone(value)))


def parse_money(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Enter a valid non-negative fee.") from exc
    if not amount.is_finite() or amount < 0 or amount > Decimal("99999999.99"):
        raise ValueError("Enter a valid non-negative fee.")
    return amount


def _validated_upload(field, destination, label):
    uploaded = request.files.get(field)
    if not uploaded or not uploaded.filename:
        return None
    extension = (
        uploaded.filename.rsplit(".", 1)[-1].lower()
        if "." in uploaded.filename
        else ""
    )
    if extension not in ALLOWED_UPLOADS:
        raise ValueError(f"Only PDF, PNG, and JPEG {label} files are allowed.")
    # Extensions are not enough. Validate common safe file signatures before
    # storing the private file; the original filename is never retained.
    signature = uploaded.stream.read(16)
    uploaded.stream.seek(0)
    valid_signature = (
        extension == "pdf" and signature.startswith(b"%PDF-")
        or extension == "png" and signature.startswith(b"\x89PNG\r\n\x1a\n")
        or extension in {"jpg", "jpeg"} and signature.startswith(b"\xff\xd8\xff")
    )
    if not valid_signature:
        raise ValueError(f"The uploaded {label} file does not match an allowed format.")
    os.makedirs(destination, exist_ok=True)
    filename = f"{secrets.token_hex(16)}.{extension}"
    path = os.path.join(destination, filename)
    uploaded.save(path)
    stage_upload(db.session, path)
    return filename


def save_upload(field):
    return _validated_upload(field, app.config["UPLOAD_FOLDER"], "evidence")


def save_valid_id_upload(field="valid_id_file"):
    return _validated_upload(
        field, app.config["IDENTIFICATION_FOLDER"], "valid ID"
    )


def save_signature_upload(field="signature_file"):
    """Store an Administrator-uploaded electronic signature outside static files."""
    uploaded = request.files.get(field)
    if not uploaded or not uploaded.filename:
        return None
    extension = uploaded.filename.rsplit(".", 1)[-1].lower() if "." in uploaded.filename else ""
    if extension not in {"png", "jpg", "jpeg"}:
        raise ValueError("An electronic signature must be a PNG or JPEG image.")
    return _validated_upload(field, app.config["SIGNATURE_FOLDER"], "signature image")


def active_requirements(permit_type_id):
    return ServiceRequirement.query.filter_by(
        permit_type_id=permit_type_id, is_active=True
    ).order_by(ServiceRequirement.position, ServiceRequirement.id).all()


def requirement_progress(application):
    records = application.requirement_records
    total = len(records)
    submitted = sum(record.status != "Not Submitted" for record in records)
    verified = sum(record.status == "Verified" for record in records)
    return {
        "total": total,
        "submitted": submitted,
        "verified": verified,
        "submitted_percent": round((submitted / total) * 100) if total else 100,
        "verified_percent": round((verified / total) * 100) if total else 100,
    }


def create_requirement_snapshot(application, definitions, resident=None):
    """Snapshot the configured requirements and resident uploads in one transaction."""
    for definition in definitions:
        value, file_path, status = requirement_submission(
            definition, request.form, request.files, save_upload, resident or actor())
        record = ApplicationRequirement(
            application_id=application.id,
            definition_id=definition.id,
            name=definition.name,
            requirement_type=definition.requirement_type,
            instructions=definition.instructions,
            is_required=definition.is_required,
            status=status,
            value=value,
            file_path=file_path,
            submitted_at=now() if status != "Not Submitted" else None,
        )
        db.session.add(record)
        db.session.flush()
        if status != "Not Submitted":
            db.session.add(RequirementHistory(
                requirement_id=record.id, actor_id=actor().id, actor_role=actor().role,
                status=status, file_path=file_path, value=value,
            ))


def permit_processor_allowed(application):
    if actor().role not in {"staff", "admin"}:
        abort(403)
    if actor().role == "staff" and application.applicant_id == actor().id:
        abort(403, "Staff members cannot process their own personal requests.")


def private_requirement_file(application, requirement):
    permit_processor_allowed(application)
    if not requirement.file_path:
        abort(404)
    filename = os.path.basename(requirement.file_path)
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"requirement-{application.reference_no}")


def masked_id_number(value):
    value = (value or "").strip()
    if not value:
        return "Not recorded"
    return "*" * max(0, len(value) - 4) + value[-4:]


def approval_readiness(profile):
    outstanding = []
    if profile.id_verification_status != "Verified":
        outstanding.append("ID verification")
    if profile.address_verification_status != "Verified":
        outstanding.append("address verification")
    return outstanding


def queue_notifications(user_id, title, message, related_type=None, related_id=None):
    recipient = db.session.get(User, user_id)
    channels = ["in_app"]
    if recipient and recipient.email:
        channels.append("email")
    if recipient and phone_valid(recipient.contact_number):
        channels.append("sms")
    for channel in channels:
        create_notification(
            user_id,
            title,
            message,
            channel,
            related_type,
            related_id,
            commit=False,
        )


def user_approval_status(user):
    if user.role not in {"resident", "staff"}:
        return "Approved"
    return user.resident_profile.approval_status if user.resident_profile else "Approved"


def acknowledgment_record(kind, record_id):
    mapping = {
        "permit": (PermitApplication, "applicant_id"),
        "event": (EventRequest, "requester_id"),
        "blotter": (BlotterCase, "complainant_id"),
    }
    if kind not in mapping:
        abort(404)
    model, owner_field = mapping[kind]
    item = db.session.get(model, record_id)
    if not item:
        abort(404)
    if actor().role == "resident" and getattr(item, owner_field) != actor().id:
        abort(403)
    if actor().role not in {"resident", "staff", "admin"}:
        abort(403)
    return item


def acknowledgment_processing_time(kind):
    setting = SystemSetting.query.filter_by(
        key=f"acknowledgment_processing_{kind}"
    ).first()
    if not setting:
        setting = SystemSetting.query.filter_by(
            key=f"receipt_processing_{kind}"
        ).first()
    defaults = {
        "permit": "1–3 business days",
        "event": "3–5 business days",
        "blotter": "1–3 business days",
    }
    return setting.value if setting and setting.value else defaults[kind]


@app.before_request
def load_user_and_csrf():
    g.user = (
        db.session.get(User, session.get("user_id"))
        if session.get("user_id")
        else None
    )
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not supplied or not secrets.compare_digest(supplied, session["csrf_token"]):
            abort(400, "Invalid or missing CSRF token.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not actor():
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        if not actor().online_access_enabled or actor().role not in {"admin", "staff", "resident"} or not actor().is_active or user_approval_status(actor()) != "Approved":
            session.clear()
            flash("Your account is not authorized for access.", "danger")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def globals_context():
    unread = (
        Notification.query.filter_by(
            user_id=actor().id, channel="in_app", read_at=None
        ).count()
        if actor()
        else 0
    )
    return {
        "current_user": actor(),
        "official_name": OFFICIAL_NAME,
        "short_name": SHORT_NAME,
        "location": LOCATION,
        "nav_items": navigation_for(actor().role) if actor() else [],
        "csrf_token": session.get("csrf_token"),
        "unread_count": unread,
        "can": can,
        "list_page": getattr(g, "list_page", None),
        "pagination_url": pagination_url,
        "mask_id_number": masked_id_number,
    }


@app.route("/")
def index():
    return redirect(url_for("dashboard") if actor() else url_for("login"))


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.get("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
        return {"status": "ok", "database": db.engine.dialect.name}, 200
    except Exception:
        db.session.rollback()
        app.logger.exception("Database health check failed")
        return {"status": "unhealthy"}, 503


@app.route("/login", methods=["GET", "POST"])
def login():
    registration_user_id = session.get("registration_user_id")
    if request.method == "GET" and registration_user_id:
        registration_user = db.session.get(User, registration_user_id)
        profile = registration_user.resident_profile if registration_user else None
        if profile and profile.approval_status == "Approved" and not profile.approval_notice_seen_at:
            flash(
                "Your account has been verified and approved. You may now sign in.",
                "success",
            )
            profile.approval_notice_seen_at = now()
            db.session.commit()
        elif profile and profile.approval_status == "Rejected":
            flash(
                "Your registration was not approved. Please contact Barangay Minante 1 for assistance.",
                "danger",
            )
    if request.method == "POST":
        value = request.form.get("username", "").strip()
        user = User.query.filter(
            or_(User.username == value, func.lower(User.email) == value.lower())
        ).first()
        if user and user.online_access_enabled and user.check_password(request.form.get("password", "")):
            status = user_approval_status(user)
            if status == "Pending":
                flash(
                    "Your account is still being verified. Please wait for Barangay Administrator approval.",
                    "warning",
                )
            elif status == "Rejected":
                flash(
                    "Your registration was not approved. Please contact Barangay Minante 1 for assistance.",
                    "danger",
                )
            elif not user.is_active:
                flash(
                    "This account is inactive. Please contact Barangay Minante 1 for assistance.",
                    "danger",
                )
            else:
                remember = bool(request.form.get("remember"))
                session.clear()
                session.permanent = remember
                session["user_id"] = user.id
                session["csrf_token"] = secrets.token_urlsafe(32)
                g.user = user
                audit("LOGIN", "authentication", str(user.id))
                db.session.commit()
                return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    audit("LOGOUT", "authentication", str(actor().id))
    db.session.commit()
    session.clear()
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    resident_types = ResidentType.query.filter_by(is_active=True).order_by(
        ResidentType.name
    ).all()
    accepted_ids = AcceptedIdType.query.filter_by(
        is_active=True, is_accepted_for_registration=True
    ).order_by(AcceptedIdType.name).all()
    if request.method == "POST":
        form = request.form
        username = form.get("username", "").strip()
        email = form.get("email", "").strip().lower()
        password = form.get("password", "")
        full_name = form.get("full_name", "").strip()
        contact = form.get("contact_number", "").strip()
        emergency = form.get("emergency_contact_number", "").strip()
        resident_type = db.session.get(
            ResidentType, form.get("resident_type_id", type=int)
        )
        accepted_id = db.session.get(
            AcceptedIdType, form.get("accepted_id_type_id", type=int)
        )
        error = None
        try:
            birth_date = datetime.strptime(form.get("date_of_birth", ""), "%Y-%m-%d").date()
            years = int(form.get("years_of_residency", ""))
            if birth_date >= date.today() or years < 0 or years > 150:
                raise ValueError
        except (ValueError, TypeError):
            birth_date, years = None, None
            error = "Enter a valid date of birth and years of residency."
        required = (
            full_name,
            form.get("date_of_birth"),
            form.get("sex"),
            form.get("civil_status"),
            contact,
            email,
            form.get("house_number_street"),
            form.get("purok_zone"),
            form.get("resident_type_id"),
            form.get("residency_status"),
            form.get("years_of_residency"),
            form.get("occupation"),
            form.get("household_number"),
            form.get("emergency_contact_name"),
            form.get("emergency_contact_relationship"),
            emergency,
            form.get("accepted_id_type_id"),
            form.get("valid_id_number"),
            username,
            password,
            form.get("confirm_password"),
        )
        if not resident_types or not accepted_ids:
            error = "Registration is temporarily unavailable until the Administrator configures Resident Types and accepted IDs."
        elif not all(required):
            error = "Complete all required registration fields."
        elif form.get("sex") not in SEX_VALUES or form.get("civil_status") not in CIVIL_STATUS_VALUES:
            error = "Select valid Personal Information values."
        elif not email_valid(email):
            error = "Enter a valid email address."
        elif not phone_valid(contact) or not phone_valid(emergency):
            error = "Enter valid contact numbers using 10 to 15 digits."
        elif (
            not resident_type
            or not resident_type.is_active
            or not accepted_id
            or not accepted_id.is_active
            or not accepted_id.is_accepted_for_registration
        ):
            error = "Select an active Resident Type and accepted Valid ID."
        elif form.get("residency_status") not in RESIDENCY_STATUS_VALUES:
            error = "Select a valid Residency Status."
        elif resident_type.name == "Others" and not form.get("resident_type_other", "").strip():
            error = "Specify the Resident Type when Others is selected."
        elif accepted_id.name == "Others" and not form.get("valid_id_type_other", "").strip():
            error = "Specify the Valid ID Type when Others is selected."
        elif not form.get("privacy_consent"):
            error = "You must agree to the Privacy Notice before registering."
        elif password != form.get("confirm_password", ""):
            error = "Passwords do not match."
        elif User.query.filter(
            or_(
                func.lower(User.username) == username.lower(),
                func.lower(User.email) == email,
            )
        ).first():
            error = "Username or email is already registered."
        elif not password_valid(password):
            error = "Password must have at least 10 characters, including a letter and number."
        family_data = None
        if not error:
            try:
                family_data = validate_family(form, registration=True)
            except ValueError as exc:
                error = str(exc)
        if error:
            flash(error, "danger")
        else:
            try:
                house = form["house_number_street"].strip()
                zone = form["purok_zone"].strip()
                valid_id_file = save_valid_id_upload()
                if not valid_id_file:
                    raise ValueError("Upload a valid ID document to complete registration.")
                user = User(
                    username=username,
                    email=email,
                    role="resident",
                    full_name=full_name,
                    contact_number=normalized_phone(contact),
                    address=f"{house}, {zone}",
                    is_active=True,
                )
                user.set_password(password)
                db.session.add(user)
                db.session.flush()
                profile = ResidentProfile(
                    user_id=user.id,
                    date_of_birth=birth_date,
                    sex=form["sex"],
                    civil_status=form["civil_status"],
                    house_number_street=house,
                    purok_zone=zone,
                    resident_type_id=resident_type.id,
                    resident_type_other=(
                        form.get("resident_type_other", "").strip() or None
                    ),
                    residency_status=form["residency_status"],
                    years_of_residency=years,
                    occupation=form["occupation"].strip(),
                    is_household_head=bool(form.get("is_household_head")),
                    household_number=form["household_number"].strip(),
                    emergency_contact_name=form["emergency_contact_name"].strip(),
                    emergency_contact_relationship=form[
                        "emergency_contact_relationship"
                    ].strip(),
                    emergency_contact_number=normalized_phone(emergency),
                    accepted_id_type_id=accepted_id.id,
                    valid_id_number=form["valid_id_number"].strip(),
                    valid_id_type_other=(
                        form.get("valid_id_type_other", "").strip() or None
                    ),
                    valid_id_file_path=valid_id_file,
                    id_verification_status="Pending Verification",
                    address_verification_status="Pending Verification",
                    privacy_consent=True,
                    privacy_consented_at=now(),
                    approval_status="Pending",
                )
                db.session.add(profile)
                db.session.flush()
                save_family(profile, family_data, user.id)
                log_activity(
                    user.id,
                    "RESIDENT_REGISTERED",
                    "Resident self-registration submitted for approval",
                    request.remote_addr,
                    "users",
                    str(user.id),
                    actor_role="resident",
                    commit=False,
                )
                log_activity(
                    user.id,
                    "ID_UPLOADED",
                    "Valid ID uploaded for Administrator verification.",
                    request.remote_addr,
                    "resident_identification",
                    str(user.id),
                    actor_role="resident",
                    commit=False,
                )
                db.session.commit()
                session["registration_user_id"] = user.id
                flash(
                    "Registration submitted successfully. Your account is pending Barangay verification and Administrator approval.",
                    "success",
                )
                return redirect(url_for("login"))
            except Exception as exc:
                db.session.rollback()
                # SQL exception tracebacks can include private submitted values.
                app.logger.error("Resident registration failed (%s)", type(exc).__name__)
                flash("Registration could not be saved. Please try again.", "danger")
    return render_template(
        "register.html", resident_types=resident_types, accepted_ids=accepted_ids,
        disability_types=DISABILITY_TYPES, family_rows=form_family_rows(request.form)
    )


@app.get("/privacy-notice")
def privacy_notice():
    return render_template("privacy_notice.html")


@app.get("/resident-identifications/<int:user_id>/document")
@login_required
def resident_identification_document(user_id):
    """Serve a private valid-ID document only to its owner or an Administrator."""
    if actor().role != "admin" and actor().id != user_id:
        abort(403)
    profile = ResidentProfile.query.filter_by(user_id=user_id).first_or_404()
    filename = os.path.basename(profile.valid_id_file_path or "")
    if not filename:
        abort(404)
    path = os.path.join(app.config["IDENTIFICATION_FOLDER"], filename)
    if not os.path.isfile(path):
        abort(404)
    inline = request.args.get("inline") == "1"
    extension = os.path.splitext(filename)[1].lower()
    response = send_file(
        path, as_attachment=not inline,
        download_name="valid-id-document" + extension,
        mimetype={".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".pdf": "application/pdf"}.get(extension, "application/octet-stream"),
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    reset_link = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter(
            func.lower(User.email) == email, User.is_active.is_(True), User.online_access_enabled.is_(True)
        ).first()
        if user:
            raw, _ = PasswordResetToken.issue(user, app.config["RESET_TOKEN_MINUTES"])
            reset_link = url_for("reset_password", token=raw, _external=True)
            create_notification(
                user.id,
                "Password reset instructions",
                f"Secure password reset instructions were issued and expire in {app.config['RESET_TOKEN_MINUTES']} minutes.",
                "email",
                commit=False,
            )
            log_activity(
                user.id,
                "PASSWORD_RESET_REQUESTED",
                "Recovery email requested",
                request.remote_addr,
                "authentication",
                str(user.id),
                commit=False,
            )
            db.session.commit()
        flash(
            "If an account is associated with that email address, password reset instructions will be sent.",
            "info",
        )
    return render_template(
        "forgot_password.html", development_reset_link=reset_link if app.debug else None
    )


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    record = PasswordResetToken.valid_for(token)
    if not record or not record.user.online_access_enabled:
        flash("This password reset link is invalid or has expired.", "danger")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if password != request.form.get("confirm_password", ""):
            flash("Passwords do not match.", "danger")
        elif not password_valid(password):
            flash(
                "Password must have at least 10 characters, including a letter and number.",
                "danger",
            )
        else:
            record.user.set_password(password)
            record.used_at = now()
            PasswordResetToken.query.filter(
                PasswordResetToken.user_id == record.user_id,
                PasswordResetToken.used_at.is_(None),
            ).update({"used_at": now()})
            log_activity(
                record.user_id,
                "PASSWORD_RESET_COMPLETED",
                "Password securely changed",
                request.remote_addr,
                "authentication",
                str(record.user_id),
                commit=False,
            )
            db.session.commit()
            flash("Password reset complete. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("reset_password.html")


@app.route("/profile", methods=["GET", "POST"])
@login_required
@permission_required("profile", "view")
def profile():
    user = actor()
    if request.method == "POST":
        if request.form.get("action", "profile") == "password":
            current = request.form.get("current_password", "")
            password = request.form.get("password", "")
            if not user.check_password(current):
                flash("Current password is incorrect.", "danger")
            elif password != request.form.get("confirm_password", ""):
                flash("New passwords do not match.", "danger")
            elif not password_valid(password):
                flash(
                    "Password must have at least 10 characters, including a letter and number.",
                    "danger",
                )
            else:
                user.set_password(password)
                audit("PASSWORD_CHANGED", "profile", str(user.id))
                db.session.commit()
                flash("Password changed securely.", "success")
        else:
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            contact = request.form.get("contact_number", "").strip()
            if not full_name or not email_valid(email) or (contact and not phone_valid(contact)):
                flash("A full name, valid email, and valid contact number are required.", "danger")
            elif User.query.filter(User.email == email, User.id != user.id).first():
                flash("That email is already registered.", "danger")
            else:
                try:
                    user.full_name = full_name
                    user.email = email
                    user.contact_number = normalized_phone(contact)
                    resident = user.resident_profile
                    if resident:
                        previous_address = (resident.house_number_street, resident.purok_zone)
                        previous_identity = (resident.accepted_id_type_id, resident.valid_id_type_other,
                                             resident.valid_id_number, resident.valid_id_file_path)
                        house = request.form.get(
                            "house_number_street", resident.house_number_street or ""
                        ).strip()
                        zone = request.form.get(
                            "purok_zone", resident.purok_zone or ""
                        ).strip()
                        user.address = ", ".join(part for part in (house, zone) if part)
                        resident.house_number_street = house or None
                        resident.purok_zone = zone or None
                        sex = request.form.get("sex", resident.sex or "")
                        civil = request.form.get("civil_status", resident.civil_status or "")
                        if sex and sex not in SEX_VALUES:
                            raise ValueError("Select a valid Sex value.")
                        if civil and civil not in CIVIL_STATUS_VALUES:
                            raise ValueError("Select a valid Civil Status.")
                        resident.sex = sex or None
                        resident.civil_status = civil or None
                        resident.occupation = request.form.get(
                            "occupation", resident.occupation or ""
                        ).strip() or None
                        resident.household_number = request.form.get(
                            "household_number", resident.household_number or ""
                        ).strip() or None
                        resident.is_household_head = bool(
                            request.form.get("is_household_head")
                        )
                        resident.emergency_contact_name = request.form.get(
                            "emergency_contact_name", resident.emergency_contact_name or ""
                        ).strip() or None
                        resident.emergency_contact_relationship = request.form.get(
                            "emergency_contact_relationship",
                            resident.emergency_contact_relationship or "",
                        ).strip() or None
                        emergency = request.form.get(
                            "emergency_contact_number",
                            resident.emergency_contact_number or "",
                        ).strip()
                        if emergency and not phone_valid(emergency):
                            raise ValueError("Enter a valid emergency contact number.")
                        resident.emergency_contact_number = normalized_phone(emergency) or None
                        if request.form.get("date_of_birth"):
                            birth_date = datetime.strptime(
                                request.form["date_of_birth"], "%Y-%m-%d"
                            ).date()
                            if birth_date >= date.today():
                                raise ValueError("Enter a valid date of birth.")
                            resident.date_of_birth = birth_date
                        if request.form.get("years_of_residency"):
                            years = int(request.form["years_of_residency"])
                            if years < 0 or years > 150:
                                raise ValueError("Enter valid years of residency.")
                            resident.years_of_residency = years
                        if request.form.get("resident_type_id"):
                            resident_type = db.session.get(
                                ResidentType,
                                request.form.get("resident_type_id", type=int),
                            )
                            if not resident_type or not resident_type.is_active:
                                raise ValueError("Select an active Resident Type.")
                            resident.resident_type_id = resident_type.id
                            resident_type_other = request.form.get(
                                "resident_type_other", ""
                            ).strip()
                            if resident_type.name == "Others" and not resident_type_other:
                                raise ValueError(
                                    "Specify the Resident Type when Others is selected."
                                )
                            resident.resident_type_other = resident_type_other or None
                        residency_status = request.form.get(
                            "residency_status", resident.residency_status or ""
                        )
                        if residency_status and residency_status not in RESIDENCY_STATUS_VALUES:
                            raise ValueError("Select a valid Residency Status.")
                        if residency_status and residency_status != resident.residency_status:
                            resident.residency_status = residency_status
                            audit(
                                "RESIDENCY_STATUS_UPDATED",
                                "resident_profile",
                                str(user.id),
                                "Resident updated current residency status.",
                            )
                        if request.form.get("accepted_id_type_id"):
                            accepted_id = db.session.get(
                                AcceptedIdType,
                                request.form.get("accepted_id_type_id", type=int),
                            )
                            if (
                                not accepted_id
                                or not accepted_id.is_active
                                or not accepted_id.is_accepted_for_registration
                            ):
                                raise ValueError("Select an accepted Valid ID.")
                            resident.accepted_id_type_id = accepted_id.id
                            id_type_other = request.form.get(
                                "valid_id_type_other", ""
                            ).strip()
                            if accepted_id.name == "Others" and not id_type_other:
                                raise ValueError(
                                    "Specify the Valid ID Type when Others is selected."
                                )
                            resident.valid_id_type_other = id_type_other or None
                        resident.valid_id_number = request.form.get(
                            "valid_id_number", resident.valid_id_number or ""
                        ).strip() or None
                        if request.files.get("valid_id_file") and request.files["valid_id_file"].filename:
                            replacement = save_valid_id_upload()
                            resident.valid_id_file_path = replacement
                            audit(
                                "ID_REPLACED",
                                "resident_identification",
                                str(user.id),
                                "Resident replaced a valid ID for review.",
                            )
                        if previous_identity != (resident.accepted_id_type_id, resident.valid_id_type_other,
                                                 resident.valid_id_number, resident.valid_id_file_path):
                            resident.id_verification_status = "Pending Verification"
                            resident.id_verified_by = resident.id_verified_at = resident.id_rejection_reason = None
                        if previous_address != (resident.house_number_street, resident.purok_zone):
                            resident.address_verification_status = "Pending Verification"
                            resident.address_verified_by = resident.address_verified_at = resident.address_rejection_reason = None
                    else:
                        user.address = request.form.get("address", "").strip()
                    audit("PROFILE_UPDATED", "profile", str(user.id))
                    db.session.commit()
                    flash("Profile updated.", "success")
                except (ValueError, TypeError):
                    db.session.rollback()
                    flash("Profile values are invalid. Review the highlighted information.", "danger")
    return render_template(
        "profile.html",
        resident_types=ResidentType.query.filter_by(is_active=True).order_by(
            ResidentType.name
        ).all(),
        accepted_ids=AcceptedIdType.query.filter_by(
            is_active=True, is_accepted_for_registration=True
        ).order_by(AcceptedIdType.name).all(),
    )


@app.route("/announcements", methods=["GET", "POST"])
@login_required
@permission_required("announcement", "view")
def announcements():
    refresh_announcements(audit)
    if request.method == "POST":
        staff_or_admin()
        try:
            title = request.form.get("title", "").strip()
            body = request.form.get("body", "").strip()
            category = request.form.get("category", "General Advisory")
            start = local_datetime(request.form.get("publish_start_at"))
            expires = local_datetime(request.form.get("expires_at"))
            if not title or not body or len(title) > 180 or category not in CATEGORIES:
                raise ValueError("Enter a title, message and valid category.")
            if expires <= start:
                raise ValueError("Expiration must follow publication.")
            approved = actor().role == "admin"
            status = ("Scheduled" if start > now() else "Published") if approved else "Draft"
            if expires <= now() and approved:
                status = "Expired"
            item = Announcement(title=title, body=body, category=category,
                publish_start_at=start, expires_at=expires, created_by=actor().id,
                status=status, is_published=approved, published_at=start,
                approved_by=actor().id if approved else None, approved_at=now() if approved else None)
            db.session.add(item)
            db.session.flush()
            audit("ANNOUNCEMENT_" + status.upper(), "announcement", str(item.id))
            db.session.commit()
            return redirect(url_for("announcements"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    query = Announcement.query if actor().role == "admin" else active_announcements()
    if actor().role == "staff":
        query = Announcement.query.filter(or_(Announcement.id.in_(active_announcements().with_entities(Announcement.id)), Announcement.created_by == actor().id))
    page = query.order_by(Announcement.published_at.desc()).paginate(per_page=20, error_out=False)
    return render_template("announcements.html", records=page.items, page=page, categories=CATEGORIES)



@app.post("/announcements/<int:record_id>/approve")
@login_required
@permission_required("announcement", "manage")
def approve_announcement(record_id):
    item = Announcement.query.get_or_404(record_id)
    if item.status != "Draft" or not item.expires_at or item.expires_at <= now():
        abort(409, "Only an unexpired draft can be approved.")
    item.status = "Scheduled" if item.publish_start_at and item.publish_start_at > now() else "Published"
    item.is_published = True
    item.published_at = item.publish_start_at or now()
    item.approved_by = actor().id
    item.approved_at = now()
    audit("ANNOUNCEMENT_APPROVED", "announcement", str(item.id), item.title)
    db.session.commit()
    return redirect(url_for("announcements"))


@app.post("/announcements/<int:record_id>/delete")
@login_required
@permission_required("announcement", "manage")
def announcement_delete(record_id):
    item = Announcement.query.get_or_404(record_id)
    reference, title = str(item.id), item.title
    item.status, item.is_published = "Archived", False
    audit("ANNOUNCEMENT_ARCHIVED", "announcement", reference, title)
    db.session.commit()
    return redirect(url_for("announcements"))


def staff_or_admin():
    if actor().role not in {"staff", "admin"}:
        abort(403)


def next_queue_number(category):
    prefix = {"Document Request": "A", "Complaint / Concern": "B"}.get(category, "C")
    today = local_today()
    latest = db.session.query(func.max(cast(func.substr(QueueEntry.queue_number, len(prefix) + 2), Integer))).filter(
        QueueEntry.queue_date == today, QueueEntry.queue_number.like(prefix + "-%")).scalar()
    number = latest + 1 if latest else 1
    return f"{prefix}-{number:06d}"


def search_residents(term):
    term = (term or "").strip()
    query = User.query.join(ResidentProfile, ResidentProfile.user_id == User.id).filter(
        User.role.in_(("resident", "staff"))
    )
    if term:
        like = f"%{term}%"
        query = query.filter(or_(
            User.id == int(term) if term.isdigit() else User.id == -1,
            User.full_name.ilike(like), User.username.ilike(like),
            User.contact_number.ilike(like), ResidentProfile.household_number.ilike(like),
            ResidentProfile.purok_zone.ilike(like),
        ))
    return query.options(contains_eager(User.resident_profile)).order_by(User.full_name).limit(50).all()


def front_desk_resident(user_id):
    """Return an eligible resident record for staff-assisted services."""
    if not user_id:
        return None
    resident = db.session.get(User, user_id)
    return (
        resident
        if resident and resident.role in {"resident", "staff"} and resident.resident_profile
        else None
    )


@app.route("/front-desk", methods=["GET", "POST"])
@login_required
def front_desk():
    staff_or_admin()
    if request.method == "POST":
        resident = front_desk_resident(request.form.get("resident_id", type=int))
        permit_type = db.session.get(PermitType, request.form.get("permit_type_id", type=int))
        purpose = request.form.get("purpose", "").strip()
        if not resident or not resident.is_active or resident.resident_profile.approval_status == "Rejected" or not permit_type or not permit_type.is_active or not purpose:
            flash("Select a valid Resident, active service, and purpose.", "danger")
        elif not permit_type.fee_is_configured or permit_type.fee is None:
            flash("This service is unavailable until the Administrator configures its fee.", "danger")
        else:
            try:
                # Obtain SQLite's writer lock before allocating a daily ticket.
                db.session.execute(text("UPDATE users SET updated_at=updated_at WHERE id=:id"), {"id": actor().id})
                item = PermitApplication(
                    applicant_id=resident.id, permit_type_id=permit_type.id, purpose=purpose,
                    fee_at_submission=permit_type.fee, request_source="Walk-in / Staff Assisted",
                    encoded_by_staff_id=actor().id, office_name=request.form.get("office_name", "").strip() or None,
                )
                db.session.add(item)
                db.session.flush()
                create_requirement_snapshot(item, active_requirements(permit_type.id), resident)
                queue = QueueEntry(
                    queue_date=local_today(), queue_number=next_queue_number("Document Request"),
                    category="Document Request", resident_id=resident.id,
                    permit_application_id=item.id, related_type="permit", related_id=item.id, created_by=actor().id,
                )
                db.session.add(queue)
                audit("WALKIN_REQUEST_CREATED", "permit", item.reference_no, "Staff-assisted resident request")
                audit("QUEUE_CREATED", "queue", queue.queue_number, item.reference_no)
                queue_notifications(resident.id, "Walk-in request received", f"{item.reference_no} was filed at the Barangay front desk.", "permit", item.id)
                db.session.commit()
                return redirect(url_for("walkin_acknowledgment", record_id=item.id))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
    term = request.args.get("q", "")
    permit_types = PermitType.query.filter_by(is_active=True).order_by(PermitType.name).all()
    requirements_by_type = {item.id: [] for item in permit_types}
    for requirement in ServiceRequirement.query.filter(
        ServiceRequirement.is_active.is_(True),
        ServiceRequirement.permit_type_id.in_(requirements_by_type),
    ).order_by(ServiceRequirement.position, ServiceRequirement.id).all():
        requirements_by_type[requirement.permit_type_id].append(requirement)
    return render_template(
        "front_desk.html", residents=search_residents(term), query=term,
        selected_resident=front_desk_resident(request.values.get("resident_id", type=int)),
        id_types=AcceptedIdType.query.filter_by(is_active=True, is_accepted_for_registration=True).all(),
        event_categories=EventCategory.query.filter_by(is_active=True).order_by(EventCategory.name).all(),
        venues=EventVenue.query.filter_by(is_active=True).order_by(EventVenue.name).all(),
        permit_types=permit_types,
        requirements_by_type=requirements_by_type,
        queues=QueueEntry.query.filter_by(queue_date=local_today()).order_by(QueueEntry.created_at).all(),
        ready_permits=PermitApplication.query.filter_by(status="Ready for Pickup").order_by(PermitApplication.updated_at.desc()).all(),
    )


@app.get("/front-desk/permits/<int:record_id>/claim-stub.docx")
@login_required
def walkin_claim_stub(record_id):
    item = db.session.get(PermitApplication, record_id)
    if not item:
        flash("The requested walk-in Permit record was not found.", "warning")
        return redirect(url_for("dashboard"))
    ensure_ownership(item.applicant_id)
    queue = QueueEntry.query.filter_by(permit_application_id=item.id).order_by(QueueEntry.id.desc()).first()
    if not queue:
        flash("A walk-in acknowledgment is unavailable because this Permit has no front-desk queue record.", "warning")
        return redirect(url_for("permit_detail", record_id=item.id))
    audit("WALKIN_ACKNOWLEDGMENT_GENERATED", "permit", item.reference_no)
    db.session.commit()
    return send_file(generate_walkin_claim_stub(item, queue), mimetype=DOCX_MIMETYPE,
                     as_attachment=True, download_name=f"walk-in-acknowledgment-{item.reference_no}.docx")


@app.get("/front-desk/permits/<int:record_id>/acknowledgment")
@login_required
def walkin_acknowledgment(record_id):
    item = PermitApplication.query.get_or_404(record_id)
    ensure_ownership(item.applicant_id)
    queue = QueueEntry.query.filter_by(permit_application_id=item.id).first_or_404()
    audit("WALKIN_ACKNOWLEDGMENT_GENERATED", "permit", item.reference_no)
    db.session.commit()
    return render_template("walkin_acknowledgment.html", item=item, queue=queue)


@app.post("/front-desk/queue/<int:queue_id>/<action>")
@login_required
def update_queue(queue_id, action):
    staff_or_admin()
    queue = QueueEntry.query.get_or_404(queue_id)
    transitions = {
        "call": ("Waiting", "Now Serving", "called_at"),
        "serve": ("Now Serving", "Serving", "served_at"),
        "complete": ("Serving", "Completed", "completed_at"),
    }
    if action not in transitions:
        abort(400)
    expected, target, timestamp_field = transitions[action]
    if queue.status != expected:
        flash(f"This queue ticket must be {expected} before it can be marked {target}.", "warning")
        return redirect(url_for("front_desk"))
    changed = QueueEntry.query.filter_by(id=queue.id, status=expected).update({"status": target}, synchronize_session=False)
    if not changed:
        db.session.rollback()
        abort(409, "The queue ticket changed. Reload before continuing.")
    queue.status = target
    setattr(queue, timestamp_field, now())
    audit("QUEUE_STATUS_UPDATED", "queue", queue.queue_number, queue.status)
    db.session.commit()
    return redirect(url_for("front_desk"))


@app.post("/front-desk/permits/<int:record_id>/release")
@login_required
def release_walkin_permit(record_id):
    staff_or_admin()
    item = PermitApplication.query.get_or_404(record_id)
    permit_processor_allowed(item)
    if item.status != "Ready for Pickup":
        abort(400, "Only a Permit that is ready for release can be released.")
    if request.form.get("identity_confirmed") != "1":
        flash("Confirm the Resident's identity before release.", "danger")
        return redirect(url_for("front_desk"))
    changed = PermitApplication.query.filter_by(id=item.id, status="Ready for Pickup").update({"status": "Completed"}, synchronize_session=False)
    if not changed:
        db.session.rollback()
        abort(409, "This document was already released.")
    item.status, item.released_by, item.released_at = "Completed", actor().id, now()
    audit("PERMIT_RELEASED", "permit", item.reference_no, "Identity confirmed at release counter.")
    queue_notifications(item.applicant_id, "Permit released", f"{item.reference_no} was released by the Barangay counter.", "permit", item.id)
    db.session.commit()
    flash("Permit release recorded.", "success")
    return redirect(url_for("front_desk"))


@app.post("/front-desk/permits/<int:record_id>/assign")
@login_required
def assign_permit(record_id):
    staff_or_admin()
    item = PermitApplication.query.get_or_404(record_id)
    assignee = db.session.get(User, request.form.get("assigned_to_user_id", type=int))
    permit_processor_allowed(item)
    if not assignee or not assignee.is_active or assignee.role not in {"staff", "admin"} or assignee.id == item.applicant_id:
        abort(400, "Select an eligible staff member or Administrator.")
    RequestAssignment.query.filter_by(permit_application_id=item.id, status="Active").update({"status": "Reassigned"})
    assignment = RequestAssignment(
        permit_application_id=item.id, assigned_to_user_id=assignee.id,
        assigned_by=actor().id, note=request.form.get("note", "").strip() or None,
    )
    db.session.add(assignment)
    audit("REQUEST_ASSIGNED", "permit", item.reference_no, f"Assigned to user {assignee.id}")
    db.session.commit()
    return redirect(url_for("permit_detail", record_id=item.id))


@app.post("/permits/<int:record_id>/feedback")
@login_required
def submit_permit_feedback(record_id):
    item = PermitApplication.query.get_or_404(record_id)
    if item.applicant_id != actor().id or item.status != "Completed":
        abort(403)
    rating = request.form.get("rating", type=int)
    if rating not in {1, 2, 3, 4, 5}:
        abort(400)
    feedback = ResidentFeedback(
        resident_id=actor().id, permit_application_id=item.id, rating=rating,
        comment=request.form.get("comment", "").strip() or None,
    )
    db.session.add(feedback)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Feedback for this completed request was already recorded.", "warning")
    return redirect(url_for("permit_detail", record_id=item.id))


@app.route("/dashboard")
@login_required
def dashboard():
    user = actor()
    refresh_announcements(audit)
    from services.revision import LOCAL_TIMEZONE
    # Appointment fields are entered/stored as Manila wall time in this legacy workflow.
    appointment_now = datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None)
    personal_only = user.role == "resident"
    permits_query = (
        PermitApplication.query.filter_by(applicant_id=user.id)
        if personal_only
        else PermitApplication.query
    )
    events_query = (
        EventRequest.query.filter_by(requester_id=user.id)
        if personal_only
        else EventRequest.query
    )
    blotters_query = (
        BlotterCase.query.filter_by(complainant_id=user.id)
        if personal_only
        else BlotterCase.query
    )
    schedules_query = (
        Schedule.query.filter_by(requested_by=user.id)
        if personal_only
        else Schedule.query
    )
    if user.role == "admin":
        stats = {
            "registered_residents": ResidentProfile.query.count(),
            "pending_resident_approvals": ResidentProfile.query.join(
                User, ResidentProfile.user_id == User.id
            ).filter(
                User.role == "resident", ResidentProfile.approval_status == "Pending"
            ).count(),
            "permit_applications": permits_query.count(),
            "event_requests": events_query.count(),
            "open_blotter_cases": blotters_query.filter(
                ~BlotterCase.status.in_(["Resolved", "Closed"])
            ).count(),
            "today_schedules": schedules_query.filter(
                func.date(Schedule.scheduled_datetime) == local_today()
            ).count(),
            "pending_final_decisions": permits_query.filter(
                PermitApplication.status == "Endorsed to Admin"
            ).count()
            + events_query.filter(EventRequest.status == "Endorsed to Admin").count(),
        }
    elif user.role == "staff":
        work_permits = permits_query.filter(PermitApplication.applicant_id != user.id)
        work_events = events_query.filter(EventRequest.requester_id != user.id)
        stats = {
            "requests_awaiting_review": work_permits.filter(
                PermitApplication.status == "Pending"
            ).count()
            + work_events.filter(EventRequest.status == "Pending").count(),
            "under_review": work_permits.filter(
                PermitApplication.status == "Under Review"
            ).count()
            + work_events.filter(EventRequest.status == "Under Review").count(),
            "new_blotter_reports": blotters_query.filter(
                BlotterCase.status == "Filed"
            ).count(),
            "endorsed_to_admin": work_permits.filter(
                PermitApplication.status == "Endorsed to Admin"
            ).count()
            + work_events.filter(EventRequest.status == "Endorsed to Admin").count(),
            "today_appointments": schedules_query.filter(
                func.date(Schedule.scheduled_datetime) == local_today()
            ).count(),
            "my_personal_requests": PermitApplication.query.filter_by(
                applicant_id=user.id
            ).count()
            + EventRequest.query.filter_by(requester_id=user.id).count(),
            "ready_for_release": work_permits.filter(PermitApplication.status == "Ready for Pickup").count(),
            "requirements_needing_correction": work_permits.filter(PermitApplication.requirement_records.any(ApplicationRequirement.status == "Needs Resubmission")).count(),
            "walkin_transactions_today": PermitApplication.query.filter(PermitApplication.request_source != "Online", func.date(PermitApplication.created_at, '+8 hours') == local_today()).count(),
        }
    else:
        active_statuses = [
            "Pending",
            "Under Review",
            "Endorsed to Admin",
            "Approved",
            "Ready for Pickup",
        ]
        stats = {
            "active_applications": permits_query.filter(
                PermitApplication.status.in_(active_statuses)
            ).count()
            + events_query.filter(EventRequest.status.in_(active_statuses)).count(),
            "blotter_reports": blotters_query.count(),
            "upcoming_appointments": schedules_query.filter(
                Schedule.scheduled_datetime >= appointment_now,
                Schedule.status.in_(["Requested", "Confirmed", "Rescheduled"]),
            ).count(),
            "unread_notifications": Notification.query.filter_by(
                user_id=user.id, channel="in_app", read_at=None
            ).count(),
        }
    display_permits, display_events = permits_query, events_query
    if user.role == "admin":
        display_permits = permits_query.filter_by(status="Endorsed to Admin")
        display_events = events_query.filter_by(status="Endorsed to Admin")
    elif user.role == "staff":
        display_permits = work_permits.filter(PermitApplication.status.in_(("Pending", "Under Review")))
        display_events = work_events.filter(EventRequest.status.in_(("Pending", "Under Review")))
    return render_template(
        "dashboard.html",
        analytics=dashboard_analytics() if user.role == "admin" else None,
        stats=stats,
        permits=display_permits.order_by(PermitApplication.application_date.desc()).limit(6).all(),
        events=display_events.order_by(EventRequest.created_at.desc()).limit(6).all(),
        my_permits=PermitApplication.query.filter_by(applicant_id=user.id)
        .order_by(PermitApplication.application_date.desc())
        .limit(6)
        .all(),
        my_events=EventRequest.query.filter_by(requester_id=user.id)
        .order_by(EventRequest.created_at.desc())
        .limit(6)
        .all(),
        schedules=schedules_query.filter(Schedule.scheduled_datetime >= appointment_now,
                                        Schedule.status.in_(("Requested", "Confirmed", "Rescheduled")))
        .order_by(Schedule.scheduled_datetime)
        .limit(5)
        .all(),
        recent_notifications=Notification.query.filter_by(user_id=user.id)
        .order_by(Notification.created_at.desc())
        .limit(3)
        .all(),
    )


def dashboard_analytics():
    from services.analytics import build_dashboard_analytics
    return build_dashboard_analytics()


@app.get("/dashboard/analytics")
@login_required
@permission_required("reports", "view")
def dashboard_analytics_fragment():
    response = app.make_response(render_template("_dashboard_analytics.html", analytics=dashboard_analytics()))
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/status")
@login_required
@permission_required("status", "view_own")
def transaction_status():
    return render_template(
        "transaction_status.html",
        permits=PermitApplication.query.filter_by(applicant_id=actor().id)
        .order_by(PermitApplication.application_date.desc())
        .all(),
        events=EventRequest.query.filter_by(requester_id=actor().id)
        .order_by(EventRequest.created_at.desc())
        .all(),
        blotters=BlotterCase.query.filter_by(complainant_id=actor().id)
        .order_by(BlotterCase.filed_date.desc())
        .all(),
        schedules=Schedule.query.filter_by(requested_by=actor().id)
        .order_by(Schedule.scheduled_datetime.desc())
        .all(),
    )


def pagination_url(number):
    args = request.args.to_dict()
    args["page"] = number
    return url_for(request.endpoint, **(request.view_args or {}), **args)


def paginate_records(query):
    g.list_page = query.paginate(per_page=30, error_out=False)
    return g.list_page.items


def scoped(query, owner_column):
    if actor().role == "resident" or (
        actor().role == "staff" and request.args.get("scope") == "mine"
    ):
        return query.filter(owner_column == actor().id)
    return query


def ensure_ownership(owner_id):
    if actor().role == "resident" and owner_id != actor().id:
        abort(403)


@app.route("/permits", methods=["GET", "POST"])
@login_required
def permits():
    if request.method == "POST":
        if not can(actor().role, "permit", "create"):
            abort(403)
        permit_type = db.session.get(
            PermitType, request.form.get("permit_type_id", type=int)
        )
        purpose = request.form.get("purpose", "").strip()
        if not permit_type or not permit_type.is_active or not purpose:
            flash("Select an active Permit Type and provide the purpose.", "danger")
        elif not permit_type.fee_is_configured or permit_type.fee is None:
            flash(
                "This Permit Type is unavailable until its fee is configured by the Administrator.",
                "danger",
            )
        else:
            try:
                definitions = active_requirements(permit_type.id)
                attachment = save_upload("attachment")
                item = PermitApplication(
                    applicant_id=actor().id,
                    permit_type_id=permit_type.id,
                    purpose=purpose,
                    fee_at_submission=permit_type.fee,
                    attachment_path=attachment,
                )
                db.session.add(item)
                db.session.flush()
                create_requirement_snapshot(item, definitions)
                action = (
                    "STAFF_PERSONAL_REQUEST_SUBMITTED"
                    if actor().role == "staff"
                    else "PERMIT_SUBMITTED"
                )
                audit(action, "permit", item.reference_no, "Personal Permit application")
                queue_notifications(
                    actor().id,
                    "Permit application received",
                    f"{item.reference_no} is Pending. Your Submission Acknowledgment is available in your account.",
                    "permit",
                    item.id,
                )
                db.session.commit()
                return redirect(
                    url_for("submission_success", kind="permit", record_id=item.id)
                )
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
            except Exception:
                db.session.rollback()
                app.logger.exception("Permit submission failed")
                flash("The Permit application could not be saved.", "danger")
    personal_scope = actor().role == "resident" or request.args.get("scope") == "mine"
    return render_template(
        "records.html",
        kind="permit",
        records=paginate_records(scoped(PermitApplication.query, PermitApplication.applicant_id)
        .order_by(PermitApplication.application_date.desc())), 
        types=PermitType.query.filter_by(is_active=True).order_by(PermitType.name).all(),
        requirements_by_type={
            permit_type.id: active_requirements(permit_type.id)
            for permit_type in PermitType.query.filter_by(is_active=True).all()
        },
        allow_create=actor().role == "resident"
        or (actor().role == "staff" and personal_scope),
        personal_scope=personal_scope,
    )


def permit_pickup_blockers(item):
    """Explain existing release-readiness rules without exposing document contents."""
    blockers = []
    incomplete = [f"{row.name} ({row.status})" for row in item.requirement_records
                  if row.is_required and row.status != "Verified"]
    if incomplete:
        blockers.append("All required evidence must be verified before pickup. Complete verification for: "
                        + "; ".join(incomplete) + ".")
    return blockers


@app.route("/permits/<int:record_id>", methods=["GET", "POST"])
@login_required
def permit_detail(record_id):
    item = PermitApplication.query.get_or_404(record_id)
    ensure_ownership(item.applicant_id)
    workflow_error_message = None
    response_status = 200
    if request.method == "POST":
        try:
            update_status(item, "permit", item.applicant_id, item.reference_no)
        except HTTPException as exc:
            if exc.code not in {400, 409}:
                raise
            db.session.rollback()
            workflow_error_message, response_status = exc.description, exc.code
            app.logger.warning("Permit update rejected permit_id=%s actor_id=%s http_status=%s reason=%s",
                               record_id, actor().id, exc.code, exc.description)
        except SQLAlchemyError as exc:
            db.session.rollback()
            sqlite_code = getattr(getattr(exc, "orig", None), "sqlite_errorcode", 0) or 0
            busy = isinstance(exc, OperationalError) and sqlite_code & 255 in {5, 6}
            response_status = 409 if busy else 500
            workflow_error_message = (
                "The database is busy. No Permit update or notifications were saved. Reload and try again."
                if busy else "The Permit update could not be saved. No changes or notifications were saved. Please try again."
            )
            app.logger.error("Permit update failed permit_id=%s error_type=%s sqlite_code=%s reason=%s",
                             record_id, type(exc).__name__, sqlite_code, workflow_error_message)
    transitions = (
        []
        if actor().role == "staff" and item.applicant_id == actor().id
        else allowed_transitions("permit", actor().role, item.status)
    )
    return render_template(
        "record_detail.html", kind="permit", record=item, transitions=transitions,
        requirement_progress=requirement_progress(item),
        workflow_error_message=workflow_error_message,
        pickup_blockers=permit_pickup_blockers(item) if "Ready for Pickup" in transitions else [],
    ), response_status


@app.get("/permits/<int:record_id>/requirements/<int:requirement_id>/file")
@login_required
def permit_requirement_file(record_id, requirement_id):
    item = PermitApplication.query.get_or_404(record_id)
    requirement = ApplicationRequirement.query.filter_by(
        id=requirement_id, application_id=item.id
    ).first_or_404()
    return private_requirement_file(item, requirement)


@app.post("/permits/<int:record_id>/requirements/<int:requirement_id>/submit")
@login_required
def resubmit_permit_requirement(record_id, requirement_id):
    item = PermitApplication.query.get_or_404(record_id)
    requirement = ApplicationRequirement.query.filter_by(
        id=requirement_id, application_id=item.id
    ).first_or_404()
    if item.applicant_id != actor().id:
        abort(403)
    if item.status in {"Approved", "Ready for Pickup", "Completed", "Rejected"}:
        abort(400, "This requirement can no longer be replaced.")
    value = request.form.get("value", "").strip() or None
    upload = request.files.get("file")
    if requirement.requirement_type == "Document" and not (upload and upload.filename):
        flash("Upload a replacement document.", "danger")
        return redirect(url_for("permit_detail", record_id=item.id))
    if requirement.requirement_type != "Document" and not value and not (upload and upload.filename):
        flash("Provide the required information before resubmitting.", "danger")
        return redirect(url_for("permit_detail", record_id=item.id))
    try:
        file_path = save_upload("file") if upload and upload.filename else requirement.file_path
        requirement.file_path = file_path
        requirement.value = value
        requirement.status = "Submitted"
        requirement.submitted_at = now()
        requirement.reviewed_by = None
        requirement.reviewed_at = None
        requirement.review_note = None
        db.session.add(RequirementHistory(
            requirement_id=requirement.id, actor_id=actor().id, actor_role=actor().role,
            status="Submitted", file_path=file_path, value=value,
            note="Replacement submitted for review.",
        ))
        audit("REQUIREMENT_SUBMITTED", "permit", item.reference_no, requirement.name)
        db.session.commit()
        flash("Replacement submitted for Barangay review.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("permit_detail", record_id=item.id))


@app.post("/permits/<int:record_id>/requirements/<int:requirement_id>/review")
@login_required
def review_permit_requirement(record_id, requirement_id):
    item = PermitApplication.query.get_or_404(record_id)
    requirement = ApplicationRequirement.query.filter_by(
        id=requirement_id, application_id=item.id
    ).first_or_404()
    permit_processor_allowed(item)
    if item.status not in {"Pending", "Under Review"} or requirement.status not in {"Submitted", "Under Review"}:
        abort(409, "Only submitted requirements on a request under review can be verified.")
    target = request.form.get("status", "")
    if target not in {"Under Review", "Verified", "Needs Resubmission"}:
        abort(400, "Invalid requirement review status.")
    if target == requirement.status:
        abort(409, "This requirement already has the selected review status.")
    note = request.form.get("note", "").strip()
    if target == "Needs Resubmission" and not note:
        flash("Provide a clear correction reason.", "danger")
        return redirect(url_for("permit_detail", record_id=item.id))
    changed = ApplicationRequirement.query.filter_by(id=requirement.id, status=requirement.status).update(
        {"status": target}, synchronize_session=False)
    if not changed:
        db.session.rollback()
        abort(409, "This requirement changed. Reload before reviewing it again.")
    requirement.status = target
    requirement.reviewed_by = actor().id
    requirement.reviewed_at = now()
    requirement.review_note = note or None
    if item.status == "Pending":
        item.status = "Under Review"
    db.session.add(RequirementHistory(
        requirement_id=requirement.id, actor_id=actor().id, actor_role=actor().role,
        status=target, note=note or None, file_path=requirement.file_path, value=requirement.value,
    ))
    event = "REQUIREMENT_VERIFIED" if target == "Verified" else (
        "REQUIREMENT_RESUBMISSION_REQUESTED" if target == "Needs Resubmission" else "REQUIREMENT_UNDER_REVIEW"
    )
    audit(event, "permit", item.reference_no, requirement.name)
    if target == "Needs Resubmission":
        queue_notifications(
            item.applicant_id, "Permit requirement needs resubmission",
            f"{item.reference_no}: {requirement.name} needs correction. {note}",
            "permit", item.id,
        )
    db.session.commit()
    flash(f"{requirement.name} marked {target}.", "success")
    return redirect(url_for("permit_detail", record_id=item.id))


def _parse_event_form():
    category = db.session.get(EventCategory, request.form.get("category_id", type=int))
    venue = db.session.get(EventVenue, request.form.get("venue_id", type=int))
    event_name = request.form.get("event_name", "").strip()
    description = request.form.get("description", "").strip()
    event_date = datetime.strptime(request.form.get("proposed_date", ""), "%Y-%m-%d").date()
    start_time = datetime.strptime(
        request.form.get("proposed_start_time", ""), "%H:%M"
    ).time()
    end_time = datetime.strptime(
        request.form.get("proposed_end_time", ""), "%H:%M"
    ).time()
    starts_at = datetime.combine(event_date, start_time)
    ends_at = datetime.combine(event_date, end_time)
    if (
        not category
        or not category.is_active
        or not venue
        or not venue.is_active
        or not event_name
        or not description
        or ends_at <= starts_at
        or starts_at <= now()
    ):
        raise ValueError("Invalid Event request values.")
    if not category.fee_is_configured or category.fee is None:
        raise ValueError(
            "This Event Category is unavailable until its fee is configured by the Administrator."
        )
    return category, venue, event_name, description, starts_at, ends_at


@app.route("/events", methods=["GET", "POST"])
@login_required
def events():
    if request.method == "POST":
        if not can(actor().role, "event", "create"):
            abort(403)
        try:
            category, venue, event_name, description, starts_at, ends_at = _parse_event_form()
            item = EventRequest(
                requester_id=actor().id,
                event_name=event_name,
                category_id=category.id,
                venue_id=venue.id,
                event_type=category.name,
                description=description,
                proposed_date=starts_at.date(),
                proposed_start_time=starts_at.time(),
                proposed_end_time=ends_at.time(),
                proposed_location=venue.name,
                expected_attendees=0,
                fee_at_submission=category.fee,
            )
            db.session.add(item)
            db.session.flush()
            reserve_event(item, venue.id, starts_at, ends_at)
            action = (
                "STAFF_PERSONAL_REQUEST_SUBMITTED"
                if actor().role == "staff"
                else "EVENT_SUBMITTED"
            )
            audit(action, "event", item.reference_no, "Personal Event request")
            queue_notifications(
                actor().id,
                "Event request received",
                f"{item.reference_no} is Pending. Your Submission Acknowledgment is available in your account.",
                "event",
                item.id,
            )
            db.session.commit()
            return redirect(
                url_for("submission_success", kind="event", record_id=item.id)
            )
        except ReservationConflict:
            db.session.rollback()
            try:
                category, venue, event_name, description, starts_at, ends_at = _parse_event_form()
                recommended = find_next_available(venue.id, starts_at, ends_at)
                if not recommended:
                    flash(
                        "The selected schedule is unavailable and no same-time alternative was found within the next year.",
                        "danger",
                    )
                    return redirect(url_for("events", scope="mine" if actor().role == "staff" else None))
                draft = {
                    "event_name": event_name,
                    "category_id": category.id,
                    "venue_id": venue.id,
                    "description": description,
                    "fee_at_submission": str(category.fee),
                }
                raw_token, hold = create_hold(
                    venue.id,
                    actor().id,
                    recommended[0],
                    recommended[1],
                    draft,
                )
                audit(
                    "EVENT_SLOT_CONFLICT_DETECTED",
                    "event",
                    None,
                    f"venue={venue.id}; requested={starts_at.isoformat()}",
                )
                audit(
                    "EVENT_SLOT_HELD",
                    "event",
                    str(hold.id),
                    f"venue={venue.id}; starts={recommended[0].isoformat()}",
                )
                create_notification(
                    actor().id,
                    "Schedule unavailable",
                    f"{venue.name} is unavailable at the selected time. A private 10-minute hold was created for {recommended[0]:%B %d, %Y from %I:%M %p} to {recommended[1]:%I:%M %p}.",
                    "in_app",
                    commit=False,
                )
                db.session.commit()
                return render_template(
                    "event_recommendation.html",
                    hold=hold,
                    hold_token=raw_token,
                    venue=venue,
                )
            except (ValueError, ReservationConflict) as exc:
                db.session.rollback()
                flash(str(exc), "warning")
        except (ValueError, TypeError) as exc:
            db.session.rollback()
            flash(
                str(exc)
                if str(exc)
                else "Provide a valid Event Category, venue, future date, and time range.",
                "danger",
            )
        except Exception:
            db.session.rollback()
            app.logger.exception("Event submission failed")
            flash("The Event request could not be saved.", "danger")
    personal_scope = actor().role == "resident" or request.args.get("scope") == "mine"
    return render_template(
        "records.html",
        kind="event",
        records=paginate_records(scoped(EventRequest.query, EventRequest.requester_id)
        .order_by(EventRequest.created_at.desc())), 
        event_categories=EventCategory.query.filter_by(is_active=True)
        .order_by(EventCategory.name)
        .all(),
        venues=EventVenue.query.filter_by(is_active=True).order_by(EventVenue.name).all(),
        allow_create=actor().role == "resident"
        or (actor().role == "staff" and personal_scope),
        personal_scope=personal_scope,
    )


@app.route("/events/<int:record_id>", methods=["GET", "POST"])
@login_required
def event_detail(record_id):
    item = EventRequest.query.get_or_404(record_id)
    ensure_ownership(item.requester_id)
    workflow_error_message = None
    response_status = 200
    if request.method == "POST":
        try:
            update_status(item, "event", item.requester_id, item.reference_no)
            return redirect(url_for("event_detail", record_id=record_id))
        except HTTPException as exc:
            if exc.code not in {400, 409}:
                raise
            db.session.rollback()
            workflow_error_message, response_status = exc.description, exc.code
            app.logger.info("Event update rejected event_id=%s actor_id=%s http_status=%s",
                            record_id, actor().id, exc.code)
        except SQLAlchemyError as exc:
            db.session.rollback()
            sqlite_code = getattr(getattr(exc, "orig", None), "sqlite_errorcode", 0) or 0
            busy = isinstance(exc, OperationalError) and sqlite_code & 255 in {5, 6}
            response_status = 409 if busy else 500
            workflow_error_message = (
                "The database is busy. No event update or notifications were saved. Reload and try again."
                if busy else "The event update could not be saved. No changes or notifications were saved. Please try again."
            )
            # Log identifiers and error class, never SQL parameters or private details.
            app.logger.error("Event update failed event_id=%s error_type=%s sqlite_code=%s",
                             record_id, type(exc).__name__, sqlite_code)
    transitions = (
        []
        if actor().role == "staff" and item.requester_id == actor().id
        else allowed_transitions("event", actor().role, item.status)
    )
    return render_template(
        "record_detail.html", kind="event", record=item, transitions=transitions,
        event_venues=EventVenue.query.filter_by(is_active=True).order_by(EventVenue.name).all(),
        workflow_error_message=workflow_error_message,
    ), response_status


@app.post("/events/recommendations/<token>/accept")
@login_required
def accept_event_recommendation(token):
    try:
        hold = get_hold(token, actor().id, lock=True)
        draft = json.loads(hold.draft_payload or "{}")
        category = db.session.get(EventCategory, draft.get("category_id"))
        venue = db.session.get(EventVenue, hold.venue_id)
        if (
            not category
            or not category.is_active
            or not category.fee_is_configured
            or category.fee is None
            or Decimal(draft.get("fee_at_submission", "-1")) != category.fee
            or not venue
            or not venue.is_active
        ):
            raise InvalidHold(
                "The Event configuration changed. Please submit the request again."
            )
        item = EventRequest(
            requester_id=actor().id,
            event_name=draft.get("event_name", ""),
            category_id=category.id,
            venue_id=venue.id,
            event_type=category.name,
            description=draft.get("description", ""),
            proposed_date=hold.starts_at.date(),
            proposed_start_time=hold.starts_at.time(),
            proposed_end_time=hold.ends_at.time(),
            proposed_location=venue.name,
            expected_attendees=0,
            fee_at_submission=category.fee,
        )
        db.session.add(item)
        db.session.flush()
        accept_hold(token, actor().id, item)
        if actor().role == "staff":
            audit(
                "STAFF_PERSONAL_REQUEST_SUBMITTED",
                "event",
                item.reference_no,
                "Personal Event request accepted from recommendation",
            )
        audit(
            "EVENT_SLOT_CONFIRMED",
            "event",
            item.reference_no,
            f"reservation={hold.id}",
        )
        queue_notifications(
            actor().id,
            "Recommended event schedule accepted",
            f"{item.reference_no} is Pending. Your Submission Acknowledgment is available.",
            "event",
            item.id,
        )
        db.session.commit()
        return redirect(url_for("submission_success", kind="event", record_id=item.id))
    except InvalidHold as exc:
        # Preserve a state change made by get_hold when a temporary hold expires.
        # A general rollback here would make the expired hold appear active again.
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("events", scope="mine" if actor().role == "staff" else None))
    except (ReservationConflict, ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("events", scope="mine" if actor().role == "staff" else None))


@app.post("/events/recommendations/<token>/decline")
@login_required
def decline_event_recommendation(token):
    try:
        hold = decline_hold(token, actor().id)
        audit("EVENT_SLOT_RELEASED", "event", str(hold.id), hold.release_reason)
        db.session.commit()
        flash(
            "The recommended schedule was released. You may choose another schedule.",
            "info",
        )
    except InvalidHold as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    return redirect(url_for("events", scope="mine" if actor().role == "staff" else None))


@app.route("/blotters", methods=["GET", "POST"])
@login_required
def blotters():
    if request.method == "POST":
        if not can(actor().role, "blotter", "create"):
            abort(403)
        try:
            incident = datetime.strptime(
                request.form.get("incident_date", ""), "%Y-%m-%dT%H:%M"
            )
            attachment = save_upload("attachment")
            item = BlotterCase(
                complainant_id=actor().id,
                respondent_name=request.form.get("respondent_name", "").strip(),
                respondent_address=request.form.get("respondent_address", "").strip()
                or None,
                incident_type=request.form.get("incident_type", "").strip(),
                incident_date=incident,
                incident_location=request.form.get("incident_location", "").strip(),
                narrative=request.form.get("narrative", "").strip(),
                supporting_document_path=attachment,
            )
            if not all(
                (
                    item.respondent_name,
                    item.incident_type,
                    item.incident_location,
                    item.narrative,
                )
            ):
                raise ValueError("Complete all required complaint fields.")
            db.session.add(item)
            db.session.flush()
            audit("BLOTTER_FILED", "blotter", item.case_no)
            queue_notifications(
                actor().id,
                "Blotter report received",
                f"{item.case_no} was filed. Your Submission Acknowledgment is available in your account.",
                "blotter",
                item.id,
            )
            db.session.commit()
            return redirect(
                url_for("submission_success", kind="blotter", record_id=item.id)
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception:
            db.session.rollback()
            app.logger.exception("Blotter submission failed")
            flash("The Blotter report could not be saved.", "danger")
    return render_template(
        "records.html",
        kind="blotter",
        records=paginate_records(scoped(BlotterCase.query, BlotterCase.complainant_id)
        .order_by(BlotterCase.filed_date.desc())), 
        allow_create=actor().role == "resident",
        personal_scope=actor().role == "resident",
    )


@app.route("/blotters/<int:record_id>", methods=["GET", "POST"])
@login_required
def blotter_detail(record_id):
    item = BlotterCase.query.get_or_404(record_id)
    ensure_ownership(item.complainant_id)
    if request.method == "POST":
        update_status(item, "blotter", item.complainant_id, item.case_no)
    return render_template(
        "record_detail.html",
        kind="blotter",
        record=item,
        transitions=allowed_transitions("blotter", actor().role, item.status),
    )


@app.route("/submissions/<kind>/<int:record_id>/success")
@login_required
def submission_success(kind, record_id):
    item = acknowledgment_record(kind, record_id)
    reference = item.case_no if kind == "blotter" else item.reference_no
    detail_endpoint = {
        "permit": "permit_detail",
        "event": "event_detail",
        "blotter": "blotter_detail",
    }[kind]
    return render_template(
        "submission_success.html",
        kind=kind,
        record=item,
        reference=reference,
        detail_endpoint=detail_endpoint,
    )


@app.get("/acknowledgments/<kind>/<int:record_id>.docx")
@login_required
def submission_receipt(kind, record_id):
    item = acknowledgment_record(kind, record_id)
    if kind == "permit" and item.request_source != "Online":
        return redirect(url_for("walkin_claim_stub", record_id=item.id))
    reference = item.case_no if kind == "blotter" else item.reference_no
    try:
        document = generate_submission_acknowledgment(
            item,
            kind,
            acknowledgment_processing_time(kind),
            "Barangay Minante 1",
            "Cauayan City, Isabela",
        )
        audit(
            "SUBMISSION_ACKNOWLEDGMENT_GENERATED",
            kind,
            reference,
            "Authorized editable Word download",
        )
        db.session.commit()
        return send_file(
            document,
            mimetype=DOCX_MIMETYPE,
            as_attachment=True,
            download_name=f"submission-acknowledgment-{reference}.docx",
        )
    except Exception:
        db.session.rollback()
        app.logger.exception("Acknowledgment generation failed for %s %s", kind, reference)
        log_activity(
            actor().id,
            "SUBMISSION_ACKNOWLEDGMENT_GENERATION_FAILED",
            "Editable Word generation failed",
            request.remote_addr,
            kind,
            reference,
            user_agent=request.headers.get("User-Agent"),
        )
        abort(500)


@app.get("/permits/<int:record_id>/approved-document.docx")
@login_required
def approved_permit_document(record_id):
    item = PermitApplication.query.get_or_404(record_id)
    ensure_ownership(item.applicant_id)
    if item.status not in {"Ready for Pickup", "Completed"}:
        flash("The approved document is not ready for download yet.", "warning")
        return redirect(url_for("permit_detail", record_id=item.id))
    signature_file = os.path.join(
        app.config["SIGNATURE_FOLDER"], os.path.basename(item.signature_path)
    ) if item.signature_path else None
    document = generate_approved_permit_document(
        item,
        item.applicant,
        item.permit_type,
        "Barangay Minante 1",
        "Cauayan City, Isabela",
        item.signatory_name,
        item.signatory_title,
        signature_file if signature_file and os.path.isfile(signature_file) else None,
    )
    audit(
        "APPROVED_PERMIT_DOCUMENT_DOWNLOADED",
        "permit",
        item.reference_no,
        "Editable Word document",
    )
    db.session.commit()
    return send_file(
        document,
        mimetype=DOCX_MIMETYPE,
        as_attachment=True,
        download_name=f"approved-{item.reference_no}.docx",
    )


@app.post("/permits/<int:record_id>/sign")
@login_required
@permission_required("permit", "approve")
def sign_approved_permit(record_id):
    item = PermitApplication.query.get_or_404(record_id)
    if item.status != "Approved" or not item.required_requirements_verified:
        abort(400, "Only a fully verified approved Permit can be signed.")
    signatory = PermitSignatory.query.filter_by(permit_type_id=item.permit_type_id).first()
    if not signatory or signatory.authorized_admin_id != actor().id:
        abort(403, "No authorized electronic-signature configuration is available for this Permit Type.")
    signature_path = os.path.join(app.config["SIGNATURE_FOLDER"], os.path.basename(signatory.file_path))
    if not os.path.isfile(signature_path):
        abort(400, "The protected electronic signature asset is unavailable.")
    item.signed_by = actor().id
    item.signed_at = now()
    item.signatory_name = signatory.name
    item.signatory_title = signatory.title
    item.signature_path = signatory.file_path
    item.document_version = (item.document_version or 0) + 1
    item.status = "Ready for Pickup"
    audit("PERMIT_SIGNED", "permit", item.reference_no, "Authorized electronic signature applied.")
    audit("PERMIT_READY_FOR_RELEASE", "permit", item.reference_no)
    queue_notifications(
        item.applicant_id,
        "Permit approved and signed",
        f"{item.reference_no} has been approved and signed. Your official Permit is ready for release and download.",
        "permit", item.id,
    )
    db.session.commit()
    flash("Electronic signature applied. The Permit is ready for release.", "success")
    return redirect(url_for("permit_detail", record_id=item.id))


def update_status(item, kind, recipient_id, reference):
    if actor().role == "resident" or (
        not can(actor().role, kind, "review") and actor().role != "admin"
    ):
        abort(403)
    owner_id = (
        item.applicant_id
        if kind == "permit"
        else item.requester_id
        if kind == "event"
        else item.complainant_id
    )
    if actor().role == "staff" and owner_id == actor().id:
        abort(403, "Staff members cannot process their own personal requests.")
    target = request.form.get("status")
    old = item.status
    if kind == "permit":
        expected = request.form.get("expected_status")
        if old == target or (expected is not None and expected != old):
            abort(409, "The Permit status has already changed. Reload and review its current status before updating. No duplicate notifications were created.")
    if kind == "event":
        expected = request.form.get("expected_status")
        if old == target or (expected is not None and expected != old):
            abort(409, "The event status has already changed. Reload and review its current status before updating. No duplicate notifications were created.")
    if not validate_transition(kind, actor().role, old, target):
        abort(400, "Invalid status transition.")
    if kind == "permit" and target == "Completed":
        abort(400, "Use the release counter to verify identity and record release.")
    if kind == "permit" and target == "Endorsed to Admin":
        if not item.required_requirements_verified:
            flash("All required requirements must be verified before endorsement.", "danger")
            return
        item.endorsed_by = actor().id
        item.endorsed_at = now()
        item.endorsement_note = request.form.get("remarks", "").strip() or None
        audit("APPLICATION_ENDORSED_TO_ADMIN", "permit", reference, item.endorsement_note)
    if kind == "permit" and target == "Approved" and not item.required_requirements_verified:
        abort(409, "All required evidence must remain verified before final approval.")
    if kind == "permit" and target == "Ready for Pickup":
        blockers = permit_pickup_blockers(item)
        if blockers:
            abort(409, " ".join(blockers))
        changed = PermitApplication.query.filter_by(id=item.id, status="Approved").update(
            {"status": target}, synchronize_session=False)
        if changed != 1:
            abort(409, "The Permit status changed. Reload before preparing it for pickup.")
        audit("PERMIT_READY_FOR_RELEASE", "permit", reference)
    if kind == "event" and target == "Approved":
        if not item.venue_id or not item.proposed_start_time or not item.proposed_end_time:
            abort(409, "Set an active venue and time range before approving this event.")
        try:
            reschedule_event(item, item.venue_id,
                datetime.combine(item.proposed_date, item.proposed_start_time),
                datetime.combine(item.proposed_date, item.proposed_end_time))
        except (ValueError, ReservationConflict) as exc:
            abort(409, str(exc))
        if item.status != old:
            abort(409, "The event status changed. Reload before approving.")
    if kind == "event":
        # Claim this transition in the same transaction as its audit/notifications.
        # SQLite serializes writers; competing stale requests cannot both win.
        changed = EventRequest.query.filter_by(id=item.id, status=old).update(
            {"status": target}, synchronize_session=False)
        if changed != 1:
            abort(409, "The event status changed during this update. Reload before trying again. No duplicate notifications were created.")
    item.status = target
    if hasattr(item, "remarks"):
        item.remarks = request.form.get("remarks", "").strip() or None
    if hasattr(item, "reviewed_by"):
        item.reviewed_by = actor().id
    if target in {"Approved", "Rejected"} and hasattr(item, "decision_date"):
        item.decision_date = now()
        if target == "Approved" and kind == "permit":
            item.decided_by = actor().id
            audit("PERMIT_APPROVED", "permit", reference, "Awaiting preparation for pickup.")
        elif target == "Rejected" and kind == "permit":
            audit("PERMIT_REJECTED", "permit", reference)
    if kind == "event" and target in {"Rejected", "Cancelled"}:
        release_event_reservation(item, f"Event request changed to {target}.")
    audit(f"{kind.upper()}_STATUS_CHANGED", kind, reference, f"{old} -> {target}")
    queue_notifications(
        recipient_id,
        f"{kind.title()} status updated",
        f"{reference}: {target}",
        kind,
        item.id,
    )
    db.session.commit()
    flash("Status updated and notifications recorded.", "success")


@app.route("/schedules", methods=["GET", "POST"])
@login_required
def schedules():
    if request.method == "POST":
        if not can(actor().role, "schedule", "create"):
            abort(403)
        selection = request.form.get("related_record", "").strip()
        related_type = related_id = None
        if selection:
            try:
                related_type, raw_id = selection.split(":", 1)
                related_id = int(raw_id)
            except (ValueError, TypeError):
                pass
        else:
            related_type = request.form.get("related_type", "").strip()
            related_id = request.form.get("related_id", type=int)
        if related_type not in {"permit", "event", "blotter"} or not related_id or related_id < 1:
            flash("Select a valid Permit, Event, or Blotter transaction.", "danger")
            return redirect(url_for("schedules"))
        try:
            scheduled = datetime.fromisoformat(
                request.form.get("scheduled_datetime", "").strip()
            )
            if scheduled.tzinfo is not None:
                raise ValueError
        except (ValueError, TypeError):
            flash("Enter a valid schedule date and time.", "danger")
            return redirect(url_for("schedules"))
        owner = related_owner(related_type, related_id)
        if owner is None:
            flash(
                "The selected transaction was not found. Refresh the page and choose an available transaction.",
                "danger",
            )
            return redirect(url_for("schedules"))
        ensure_ownership(owner)
        item = Schedule(
            related_type=related_type,
            related_id=related_id,
            requested_by=owner,
            scheduled_datetime=scheduled,
            purpose=request.form.get("purpose", "").strip() or "Barangay transaction",
        )
        db.session.add(item)
        db.session.flush()
        audit("SCHEDULE_CREATED", "schedule", item.reference_no)
        queue_notifications(
            owner,
            "Schedule requested",
            f"{item.reference_no}: {scheduled:%b %d, %Y %I:%M %p}",
            "schedule",
            item.id,
        )
        db.session.commit()
        flash("Schedule request created successfully.", "success")
        return redirect(url_for("schedules"))
    query = (
        Schedule.query.filter_by(requested_by=actor().id)
        if actor().role == "resident"
        else Schedule.query
    )
    choices = schedule_choices()
    return render_template(
        "schedules.html",
        records=paginate_records(query.order_by(Schedule.scheduled_datetime)),
        schedule_choices=choices,
        choice_count=sum(len(records) for records in choices.values()),
        schedule_transitions=lambda status: allowed_transitions("schedule", actor().role, status),
    )


def schedule_choices():
    permit_query = PermitApplication.query
    event_query = EventRequest.query
    blotter_query = BlotterCase.query
    if actor().role == "resident":
        permit_query = permit_query.filter_by(applicant_id=actor().id)
        event_query = event_query.filter_by(requester_id=actor().id)
        blotter_query = blotter_query.filter_by(complainant_id=actor().id)
    return {
        "permits": permit_query.order_by(PermitApplication.application_date.desc()).all(),
        "events": event_query.order_by(EventRequest.created_at.desc()).all(),
        "blotters": blotter_query.order_by(BlotterCase.filed_date.desc()).all(),
    }


def related_owner(kind, record_id):
    model, column = {
        "permit": (PermitApplication, "applicant_id"),
        "event": (EventRequest, "requester_id"),
        "blotter": (BlotterCase, "complainant_id"),
    }[kind]
    item = db.session.get(model, record_id)
    return getattr(item, column) if item else None


@app.post("/schedules/<int:record_id>/status")
@login_required
def schedule_status(record_id):
    item = Schedule.query.get_or_404(record_id)
    ensure_ownership(item.requested_by)
    if actor().role == "resident":
        abort(403)
    target, old = request.form.get("status", "").strip(), item.status
    if not validate_transition("schedule", actor().role, old, target):
        choices = allowed_transitions("schedule", actor().role, old)
        app.logger.warning(
            "Rejected schedule status update: schedule_id=%s actor_id=%s role=%s current_status=%s reason=%s",
            item.id, actor().id, actor().role, old,
            "missing_status" if not target else "disallowed_transition",
        )
        if not choices:
            abort(400, "This schedule has no further status updates. Reload the Schedules page.")
        abort(400, "Choose a valid next status: " + ", ".join(choices) + ". Reload the Schedules page if it has changed.")
    item.status = target
    item.confirmed_by = actor().id
    audit(
        "SCHEDULE_STATUS_CHANGED",
        "schedule",
        item.reference_no,
        f"{old} -> {target}",
    )
    queue_notifications(
        item.requested_by,
        "Schedule updated",
        f"{item.reference_no}: {target}",
        "schedule",
        item.id,
    )
    db.session.commit()
    app.logger.info("Schedule status saved: schedule_id=%s actor_id=%s from=%s to=%s",
                    item.id, actor().id, old, target)
    flash(f"Schedule status updated to {target}.", "success")
    return redirect(url_for("schedules"))


@app.route("/notifications")
@login_required
def notifications():
    return render_template(
        "notifications.html",
        records=paginate_records(Notification.query.filter_by(user_id=actor().id)
        .order_by(Notification.created_at.desc())),
        all_users=User.query.filter_by(is_active=True).order_by(User.full_name).all()
        if actor().role == "admin"
        else [],
    )


@app.post("/notifications/send")
@login_required
@permission_required("notification", "create")
def notification_send():
    recipient = db.session.get(User, request.form.get("user_id", type=int))
    channel = request.form.get("channel", "in_app")
    title = request.form.get("title", "").strip()
    message = request.form.get("message", "").strip()
    if not recipient or not title or not message or channel not in {"sms", "email", "in_app"}:
        abort(400, "Valid recipient, channel, title, and message are required.")
    item = create_notification(
        recipient.id, title, message, channel, commit=False
    )
    audit(
        "NOTIFICATION_SENT",
        "notification",
        str(item.id),
        f"recipient={recipient.id}; channel={channel}",
    )
    db.session.commit()
    flash("Notification recorded through the configured channel.", "success")
    return redirect(url_for("notifications"))


@app.post("/notifications/<int:record_id>/read")
@login_required
def notification_read(record_id):
    item = Notification.query.filter_by(
        id=record_id, user_id=actor().id
    ).first_or_404()
    item.read_at = now()
    db.session.commit()
    return redirect(url_for("notifications"))


@app.post("/notifications/read-all")
@login_required
def notifications_read_all():
    Notification.query.filter_by(
        user_id=actor().id, channel="in_app", read_at=None
    ).update({"read_at": now()})
    db.session.commit()
    return redirect(url_for("notifications"))


@app.route("/users", methods=["GET", "POST"])
@login_required
@permission_required("users", "view")
def users():
    if request.method == "POST":
        if actor().role != "admin":
            abort(403)
        role = request.form.get("role")
        password = request.form.get("password", "")
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        if (
            role not in {"resident", "staff", "admin"}
            or not password_valid(password)
            or not username
            or not full_name
            or not email_valid(email)
        ):
            flash(
                "Provide a unique username, valid email, full name, role, and strong password.",
                "danger",
            )
        elif User.query.filter(
            or_(
                func.lower(User.username) == username.lower(),
                func.lower(User.email) == email,
            )
        ).first():
            flash("Username or email is already registered.", "danger")
        else:
            item = User(
                username=username, email=email, full_name=full_name, role=role
            )
            item.set_password(password)
            db.session.add(item)
            db.session.flush()
            if role in {"resident", "staff"}:
                db.session.add(
                    ResidentProfile(
                        user_id=item.id,
                        approval_status="Approved",
                        reviewed_by=actor().id,
                        reviewed_at=now(),
                        approval_remarks="Account created by Administrator.",
                    )
                )
            audit("USER_CREATED", "users", str(item.id), f"role={role}")
            db.session.commit()
            flash("User account created.", "success")
    return render_template(
        "users.html", records=paginate_records(User.query.order_by(User.created_at.desc()))
    )


@app.get("/users/<int:record_id>")
@login_required
@permission_required("users", "manage")
def user_review(record_id):
    item = User.query.get_or_404(record_id)
    profile = item.resident_profile
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    return render_template(
        "user_review.html",
        record=item,
        profile=profile,
        id_preview_is_image=bool(
            profile
            and profile.valid_id_file_path
            and os.path.splitext(profile.valid_id_file_path)[1].lower()
            in image_extensions
        ),
        approval_outstanding=(
            approval_readiness(profile)
            if profile
            else ["resident profile"]
        ),
    )


@app.post("/users/<int:record_id>/verification/<kind>")
@login_required
@permission_required("users", "manage")
def user_verification(record_id, kind):
    if actor().role != "admin":
        abort(403)
    item = User.query.get_or_404(record_id)
    profile = item.resident_profile
    reason = request.form.get("reason", "").strip()
    action = (
        request.form.get("action")
        or request.form.get("verification_action")
    )
    if item.role not in {"resident", "staff"} or not profile or kind not in {"id", "address"}:
        flash("This account is not eligible for resident verification.", "warning")
        return redirect(url_for("users"))
    if action not in {"verify", "reject"}:
        flash("Choose Verify or Reject.", "warning")
        return redirect(url_for("user_review", record_id=item.id))
    if action == "reject" and not reason:
        flash("Provide a safe correction reason when rejecting verification.", "warning")
        return redirect(url_for("user_review", record_id=item.id))
    current_status = (
        profile.id_verification_status if kind == "id"
        else profile.address_verification_status
    )
    if current_status != "Pending Verification":
        flash(
            f"{kind.title()} verification is already finalized as {current_status}. "
            "A resident correction is required before another review.",
            "warning",
        )
        return redirect(url_for("user_review", record_id=item.id))
    if kind == "id":
        if action == "verify" and (not profile.valid_id_file_path or not os.path.isfile(
            os.path.join(app.config["IDENTIFICATION_FOLDER"], os.path.basename(profile.valid_id_file_path))
        )):
            flash(
                "No uploaded valid ID is available. Ask the resident to upload or replace their ID before verification.",
                "warning",
            )
            return redirect(url_for("user_review", record_id=item.id))
    status_field = "id_verification_status" if kind == "id" else "address_verification_status"
    target_status = "Verified" if action == "verify" else "Rejected"
    changed = ResidentProfile.query.filter(
        ResidentProfile.id == profile.id,
        getattr(ResidentProfile, status_field) == "Pending Verification",
    ).update({status_field: target_status}, synchronize_session=False)
    if not changed:
        db.session.rollback()
        flash("This verification was already finalized. Reload to see the current decision.", "warning")
        return redirect(url_for("user_review", record_id=item.id))
    setattr(profile, status_field, target_status)
    if kind == "id":
        profile.id_verified_by = actor().id
        profile.id_verified_at = now()
        profile.id_rejection_reason = reason or None
        event = "ID_VERIFIED" if action == "verify" else "ID_REJECTED"
    else:
        profile.address_verified_by = actor().id
        profile.address_verified_at = now()
        profile.address_rejection_reason = reason or None
        event = "ADDRESS_VERIFIED" if action == "verify" else "ADDRESS_REJECTED"
    audit(event, "resident_profile", str(item.id), reason or "Verification completed.")
    db.session.commit()
    flash(f"{'ID' if kind == 'id' else 'Address'} verification {target_status.lower()}.", "success" if action == "verify" else "warning")
    return redirect(url_for("user_review", record_id=item.id))


@app.post("/users/<int:record_id>/approval")
@login_required
@permission_required("users", "manage")
def user_approval(record_id):
    item = User.query.get_or_404(record_id)
    profile = item.resident_profile
    action = request.form.get("action") or request.form.get("approval_action")
    if item.role != "resident" or not profile:
        flash("Only Resident registrations can use this approval workflow.", "warning")
        return redirect(url_for("users"))
    if action not in {"approve", "reject"}:
        flash("Choose Approve or Reject.", "warning")
        return redirect(url_for("user_review", record_id=item.id))
    if profile.approval_status != "Pending":
        flash(
            f"This account decision was already finalized as {profile.approval_status}. "
            "It cannot be submitted again.",
            "warning",
        )
        return redirect(url_for("user_review", record_id=item.id))
    outstanding = approval_readiness(profile)
    if action == "approve" and outstanding:
        flash(
            "Account cannot be approved until " + " and ".join(outstanding) + " are Verified.",
            "danger",
        )
        return redirect(url_for("user_review", record_id=item.id))
    old = profile.approval_status
    target_status = "Approved" if action == "approve" else "Rejected"
    decision_query = ResidentProfile.query.filter_by(id=profile.id, approval_status="Pending")
    if action == "approve":
        decision_query = decision_query.filter_by(id_verification_status="Verified", address_verification_status="Verified")
    changed = decision_query.update({"approval_status": target_status}, synchronize_session=False)
    if not changed:
        db.session.rollback()
        flash("This account decision or its verification changed. Reload before continuing.", "warning")
        return redirect(url_for("user_review", record_id=item.id))
    profile.approval_status = target_status
    profile.reviewed_by = actor().id
    profile.reviewed_at = now()
    profile.approval_remarks = request.form.get("remarks", "").strip() or None
    profile.approval_notice_seen_at = None
    event = "ACCOUNT_APPROVED" if action == "approve" else "ACCOUNT_REJECTED"
    audit(event, "users", str(item.id), f"{old} -> {profile.approval_status}")
    if action == "approve":
        queue_notifications(
            item.id,
            "Resident account approved" if item.online_access_enabled else "Resident record approved",
            "Your account has been verified and approved. You may now sign in." if item.online_access_enabled else
            "Your resident record is verified. Visit the Barangay front desk with your reference for assistance.",
            "users",
            item.id,
        )
    else:
        queue_notifications(
            item.id,
            "Resident registration update",
            "Your registration was not approved. Please contact Barangay Minante 1 for assistance.",
            "users",
            item.id,
        )
    db.session.commit()
    flash(
        ("Account approved. The user may now log in." if item.online_access_enabled else
         "Resident record approved. Continue assistance at the front desk; online sign-in remains disabled.")
        if action == "approve"
        else "Account rejected. The user should go to the front desk for assistance.",
        "success" if action == "approve" else "warning",
    )
    return redirect(url_for("user_review", record_id=item.id))


@app.post("/users/<int:record_id>/role")
@login_required
@permission_required("users", "manage")
def user_role(record_id):
    item = User.query.get_or_404(record_id)
    role = request.form.get("role")
    if role not in {"resident", "staff", "admin"}:
        abort(400, "Invalid role.")
    if item.id == actor().id and role != "admin":
        abort(400, "You cannot remove your own Administrator role.")
    old = item.role
    item.role = role
    if role in {"resident", "staff"} and not item.resident_profile:
        db.session.add(
            ResidentProfile(
                user_id=item.id,
                approval_status="Approved",
                reviewed_by=actor().id,
                reviewed_at=now(),
                approval_remarks="Legacy account approved during authorized role assignment.",
            )
        )
    audit("USER_ROLE_CHANGED", "users", str(item.id), f"{old} -> {role}")
    db.session.commit()
    return redirect(url_for("users"))


@app.post("/users/<int:record_id>/status")
@login_required
@permission_required("users", "manage")
def user_account_status(record_id):
    item = User.query.get_or_404(record_id)
    if item.id == actor().id:
        abort(400, "You cannot deactivate your own account.")
    item.is_active = not item.is_active
    audit(
        "USER_STATUS_CHANGED",
        "users",
        str(item.id),
        "active" if item.is_active else "inactive",
    )
    db.session.commit()
    return redirect(url_for("users"))


@app.route("/reports")
@login_required
@permission_required("reports", "view")
def reports():
    from services.analytics import build_scope_reports
    report = build_scope_reports()
    audit("REPORT_VIEWED", "reports", "overall", "All-time core service reports")
    db.session.commit()
    return render_template("reports.html", report=report)


@app.post("/reports/export/<fmt>")
@login_required
@permission_required("reports", "export")
def report_export(fmt):
    if fmt != "pdf":
        abort(404)
    from services.analytics import build_scope_reports, pdf_report
    filters = {}
    data = build_scope_reports()
    document = pdf_report(data, actor().full_name)
    report = Report(
        generated_by=actor().id,
        report_type="overall",
        format="pdf",
        filters_json=json.dumps(filters),
    )
    db.session.add(report)
    audit("REPORT_EXPORTED", "reports", None, "pdf")
    audit("REPORT_EXPORTED_PDF", "reports", "overall", json.dumps(filters))
    audit("OVERALL_BARANGAY_REPORT_GENERATED", "reports", "overall", json.dumps(filters))
    db.session.commit()
    return send_file(
        document,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="barangay-system-report.pdf",
    )


@app.get("/reports/chart/<int:index>.png")
@login_required
@permission_required("reports", "view")
def report_chart(index):
    from services.analytics import build_scope_reports, chart_png
    report = build_scope_reports()
    if index < 0 or index >= len(report["sections"]):
        abort(404)
    chart = chart_png(report["sections"][index])
    if chart is None:
        abort(404)
    response = send_file(chart, mimetype="image/png")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/activity-logs")
@login_required
@permission_required("activity_logs", "view_logs")
def activity_logs():
    return render_template(
        "activity_logs.html",
        records=paginate_records(ActivityLog.query.order_by(ActivityLog.timestamp.desc())),
    )


def _active_from_form(default=True):
    values = request.form.getlist("is_active")
    if not values:
        return default
    return values[0].lower() not in {"0", "false", "off"}


@app.route("/configuration", methods=["GET", "POST"])
@login_required
@permission_required("configuration", "manage")
def configuration():
    if request.method == "POST":
        kind = request.form.get("kind")
        name = request.form.get("name", "").strip()
        record_id = request.form.get("record_id", type=int)
        item = None
        try:
            if kind == "permit" and name:
                item = db.session.get(PermitType, record_id) if record_id else PermitType()
                if not item:
                    raise ValueError("Permit Type not found.")
                item.name = name
                item.description = request.form.get("description", "").strip() or None
                item.requirements = request.form.get("requirements", "").strip() or None
                item.processing_time = request.form.get("processing_time", "").strip() or None
                item.fee = parse_money(request.form.get("fee"))
                item.fee_is_configured = True
                item.is_active = _active_from_form()
                db.session.add(item)
            elif kind == "requirement":
                permit_type_id = request.form.get("permit_type_id", type=int)
                if not permit_type_id or not db.session.get(PermitType, permit_type_id):
                    raise ValueError("Select a valid Permit Service.")
                item = db.session.get(ServiceRequirement, record_id) if record_id else ServiceRequirement()
                if not item:
                    raise ValueError("Requirement definition not found.")
                requirement_type = request.form.get("requirement_type", "Document")
                if requirement_type not in {"Document", "Text", "System Check"}:
                    raise ValueError("Select a valid requirement type.")
                item.permit_type_id = permit_type_id
                item.name = name
                item.requirement_type = requirement_type
                item.instructions = request.form.get("instructions", "").strip() or None
                item.is_required = request.form.get("is_required") == "1"
                item.is_active = _active_from_form()
                item.position = request.form.get("position", type=int) or 0
                db.session.add(item)
            elif kind == "signatory":
                permit_type_id = request.form.get("permit_type_id", type=int)
                if not permit_type_id or not db.session.get(PermitType, permit_type_id):
                    raise ValueError("Select a valid Permit Service.")
                item = PermitSignatory.query.filter_by(permit_type_id=permit_type_id).first()
                if not item:
                    item = PermitSignatory(permit_type_id=permit_type_id)
                signature = save_signature_upload()
                if not signature and not item.file_path:
                    raise ValueError("Upload the authorized electronic signature image.")
                item.authorized_admin_id = actor().id
                item.name = name
                item.title = request.form.get("title", "").strip()
                if not item.name or not item.title:
                    raise ValueError("Enter the authorized signatory name and title.")
                item.file_path = signature or item.file_path
                db.session.add(item)
            elif kind == "event" and name:
                item = db.session.get(EventCategory, record_id) if record_id else EventCategory()
                if not item:
                    raise ValueError("Event Category not found.")
                item.name = name
                item.description = request.form.get("description", "").strip() or None
                item.fee = parse_money(request.form.get("fee"))
                item.fee_is_configured = True
                item.is_active = _active_from_form()
                db.session.add(item)
            elif kind == "venue" and name:
                item = db.session.get(EventVenue, record_id) if record_id else EventVenue()
                if not item:
                    raise ValueError("Event Venue not found.")
                item.name = name
                item.address = request.form.get("address", "").strip() or None
                item.description = request.form.get("description", "").strip() or None
                item.is_active = _active_from_form()
                db.session.add(item)
            elif kind == "resident_type" and name:
                item = db.session.get(ResidentType, record_id) if record_id else ResidentType()
                if not item:
                    raise ValueError("Resident Type not found.")
                item.name = name
                item.description = request.form.get("description", "").strip() or None
                item.is_active = _active_from_form()
                db.session.add(item)
            elif kind == "accepted_id" and name:
                item = (
                    db.session.get(AcceptedIdType, record_id)
                    if record_id
                    else AcceptedIdType()
                )
                if not item:
                    raise ValueError("Accepted ID Type not found.")
                item.name = name
                item.description = request.form.get("description", "").strip() or None
                item.is_active = _active_from_form()
                accepted_values = request.form.getlist("is_accepted_for_registration")
                item.is_accepted_for_registration = bool(
                    accepted_values
                    and accepted_values[0].lower() not in {"0", "false", "off"}
                )
                db.session.add(item)
            elif kind == "acknowledgment":
                for acknowledgment_kind in ("permit", "event", "blotter"):
                    value = request.form.get(
                        f"processing_{acknowledgment_kind}", ""
                    ).strip()
                    if not value:
                        continue
                    setting = SystemSetting.query.filter_by(
                        key=f"acknowledgment_processing_{acknowledgment_kind}"
                    ).first()
                    if setting:
                        setting.value = value
                    else:
                        db.session.add(
                            SystemSetting(
                                key=f"acknowledgment_processing_{acknowledgment_kind}",
                                value=value,
                            )
                        )
            else:
                raise ValueError("Complete the required configuration fields.")
            db.session.flush()
            audit(
                "SERVICE_PRICE_UPDATED" if kind in {"permit", "event"} else "REQUIREMENT_CONFIGURATION_UPDATED" if kind == "requirement" else "CONFIGURATION_CHANGED",
                "configuration",
                str(item.id) if item else None,
                f"kind={kind}",
            )
            db.session.commit()
            flash("Configuration saved.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception:
            db.session.rollback()
            app.logger.exception("Configuration update failed")
            flash(
                "Configuration could not be saved. Check for duplicate names and try again.",
                "danger",
            )
    return render_template(
        "configuration.html",
        permit_types=PermitType.query.order_by(PermitType.name).all(),
        service_requirements=ServiceRequirement.query.order_by(ServiceRequirement.permit_type_id, ServiceRequirement.position, ServiceRequirement.id).all(),
        signatories=PermitSignatory.query.order_by(PermitSignatory.permit_type_id).all(),
        event_categories=EventCategory.query.order_by(EventCategory.name).all(),
        venues=EventVenue.query.order_by(EventVenue.name).all(),
        resident_types=ResidentType.query.order_by(ResidentType.name).all(),
        accepted_ids=AcceptedIdType.query.order_by(AcceptedIdType.name).all(),
        acknowledgment_processing=acknowledgment_processing_time,
    )


@app.errorhandler(405)
@app.errorhandler(409)
@app.errorhandler(413)
@app.errorhandler(422)
def workflow_error(error):
    db.session.rollback()
    return render_template("error.html", code=error.code, message=error.description), error.code


@app.errorhandler(400)
def bad_request(error):
    return (
        render_template(
            "error.html",
            code=400,
            message=getattr(error, "description", "Invalid request."),
        ),
        400,
    )


@app.errorhandler(401)
def unauthorized(_error):
    return redirect(url_for("login"))


@app.errorhandler(403)
def forbidden(error):
    return (
        render_template(
            "error.html",
            code=403,
            message=getattr(
                error,
                "description",
                "You do not have permission to access this resource.",
            ),
        ),
        403,
    )


@app.errorhandler(404)
def missing(_error):
    return (
        render_template(
            "error.html", code=404, message="The requested page was not found."
        ),
        404,
    )


@app.errorhandler(500)
def server_error(_error):
    db.session.rollback()
    return (
        render_template(
            "error.html",
            code=500,
            message="An unexpected error occurred. Please try again.",
        ),
        500,
    )


@app.cli.command("init-db")
def init_db():
    """Create or upgrade the schema without deleting existing records."""
    upgrade_legacy_schema()
    print("Database initialized non-destructively; existing users preserved.")


@app.cli.command("create-admin")
def create_admin():
    """Idempotently establish the permanent Administrator from environment values."""
    upgrade_legacy_schema()
    if User.query.filter_by(role="admin").first():
        print("Permanent Administrator already exists; preserved.")
        return
    username = os.environ.get("INITIAL_ADMIN_USERNAME")
    email = os.environ.get("INITIAL_ADMIN_EMAIL")
    password = os.environ.get("INITIAL_ADMIN_PASSWORD")
    full_name = os.environ.get("INITIAL_ADMIN_NAME", "System Administrator")
    if not username or not email or not email_valid(email) or not password_valid(password):
        raise RuntimeError(
            "Set INITIAL_ADMIN_USERNAME, INITIAL_ADMIN_EMAIL, and a strong INITIAL_ADMIN_PASSWORD."
        )
    user = User(
        username=username,
        email=email.lower(),
        full_name=full_name,
        role="admin",
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    print("Permanent Administrator created securely.")



from revision_routes import register_revision_routes
register_revision_routes(app, login_required, actor, audit, save_upload, queue_notifications)
from event_calendar_routes import register_event_calendar_routes
register_event_calendar_routes(app, login_required, actor, audit, queue_notifications)
from walkin_routes import register_walkin_routes
register_walkin_routes(app, login_required, actor, audit, save_valid_id_upload,
                       save_upload, queue_notifications, next_queue_number,
                       front_desk_resident, _parse_event_form, front_desk)

app.jinja_env.filters["local_time"] = local_display
app.jinja_env.globals["payment_status"] = payment_status

if __name__ == "__main__":
    with app.app_context():
        upgrade_legacy_schema()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
