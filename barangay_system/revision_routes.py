"""Payment routes using the existing authentication layer."""
from decimal import Decimal

from flask import abort, redirect, render_template, request, send_file, url_for
from sqlalchemy.exc import IntegrityError
from models import db, User, PermitApplication, EventRequest, Payment, PaymentAuthority
from services.revision import can_record_payment, payment_status
from services.documentgen import DOCX_MIMETYPE, generate_system_report


def register_revision_routes(app, login_required, actor, audit, save_upload, notify):
    def administrator():
        if actor().role != "admin":
            abort(403)

    @app.route("/payments", methods=["GET", "POST"])
    @login_required
    def payments():
        if request.method == "POST":
            if not can_record_payment(actor()):
                abort(403)
            kind = request.form.get("kind")
            model = {"permit": PermitApplication, "event": EventRequest}.get(kind)
            if not model:
                abort(400)
            item = model.query.get_or_404(request.form.get("request_id", type=int))
            owner = item.applicant_id if kind == "permit" else item.requester_id
            if owner == actor().id:
                abort(403, "You cannot record your own payment.")
            if item.status not in {"Approved", "Ready for Pickup"} or payment_status(item) != "Unpaid":
                abort(409, "Only approved unpaid requests can receive a payment decision.")
            status = request.form.get("status", "Paid")
            notes = request.form.get("notes", "").strip()
            if status not in {"Paid", "Waived"}:
                abort(400)
            if status == "Waived" and (actor().role != "admin" or not notes):
                abort(403, "An Administrator and a waiver reason are required.")
            try:
                amount = Decimal(request.form.get("amount", "0"))
                if not amount.is_finite() or amount != (item.fee_at_submission if status == "Paid" else 0):
                    raise ValueError()
            except (ValueError, ArithmeticError):
                abort(400, "Record the exact fee amount, or zero for an authorized waiver.")
            method = request.form.get("method", "Cash")
            if method != "Cash":
                abort(400, "Only manual cash payments are configured.")
            payment = Payment(resident_id=owner, fee_snapshot=item.fee_at_submission,
                amount_paid=amount, status=status, payment_method=method if status == "Paid" else None,
                recorded_by=actor().id, notes=notes or None,
                **{kind + "_id": item.id})
            db.session.add(payment)
            try:
                db.session.flush()
                if kind == "permit" and status == "Paid":
                    item.payment_confirmed_at = payment.paid_at
                    item.payment_confirmed_by = actor().id
                    item.payment_reference = payment.payment_reference
                audit("PAYMENT_RECORDED" if status == "Paid" else "PAYMENT_STATUS_UPDATED", "payment", payment.payment_reference, status)
                notify(owner, "Payment status updated", f"{item.reference_no}: {status}", kind, item.id)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                abort(409, "A payment decision has already been recorded.")
            return redirect(url_for("payments"))
        query = Payment.query
        permits = PermitApplication.query.filter(PermitApplication.fee_at_submission > 0,
            PermitApplication.status.in_(("Approved", "Ready for Pickup")), ~PermitApplication.payment.has())
        events = EventRequest.query.filter(EventRequest.fee_at_submission > 0,
            EventRequest.status == "Approved", ~EventRequest.payment.has())
        if actor().role == "resident" or (actor().role == "staff" and not can_record_payment(actor())):
            query = query.filter_by(resident_id=actor().id)
            permits = permits.filter_by(applicant_id=actor().id)
            events = events.filter_by(requester_id=actor().id)
        return render_template("payments.html", page=query.order_by(Payment.paid_at.desc()).paginate(per_page=30, error_out=False),
            unpaid_permits=permits.limit(50).all(), unpaid_events=events.limit(50).all(),
            authorized=can_record_payment(actor()), staff=User.query.filter_by(role="staff", is_active=True).all() if actor().role == "admin" else [],
            authorities={r.user_id for r in PaymentAuthority.query.all()} if actor().role == "admin" else set())

    @app.post("/payments/authority")
    @login_required
    def payment_authority():
        administrator()
        user = User.query.get_or_404(request.form.get("user_id", type=int))
        if user.role != "staff" or not user.is_active:
            abort(400)
        existing = db.session.get(PaymentAuthority, user.id)
        if request.form.get("grant") == "1":
            if not existing:
                db.session.add(PaymentAuthority(user_id=user.id, granted_by=actor().id))
        elif existing:
            db.session.delete(existing)
        audit("PAYMENT_AUTHORITY_UPDATED", "user", str(user.id), "Granted" if request.form.get("grant") == "1" else "Revoked")
        db.session.commit()
        return redirect(url_for("payments"))

    @app.get("/payments/<int:record_id>/receipt.docx")
    @login_required
    def payment_receipt(record_id):
        item = Payment.query.get_or_404(record_id)
        if item.resident_id != actor().id and not can_record_payment(actor()):
            abort(403)
        if item.status != "Paid" or item.amount_paid <= 0:
            abort(409, "A payment receipt requires an actual recorded payment.")
        transaction = item.permit or item.event
        from services.revision import local_display
        document = generate_system_report("Payment Receipt", ["Field", "Recorded value"], [
            ["Payment reference", item.payment_reference], ["Request reference", transaction.reference_no],
            ["Resident", item.resident.full_name],
            ["Service", item.permit.permit_type.name if item.permit else item.event.event_name],
            ["Amount paid (PHP)", item.amount_paid], ["Paid at (Asia/Manila)", local_display(item.paid_at)],
            ["Payment method", item.payment_method], ["Recorded by", item.recorder.full_name]])
        audit("PAYMENT_RECEIPT_GENERATED", "payment", item.payment_reference)
        db.session.commit()
        return send_file(document, mimetype=DOCX_MIMETYPE, as_attachment=True, download_name=f"payment-{item.payment_reference}.docx")
