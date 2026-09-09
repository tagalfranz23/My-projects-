import os
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT


def generate_permit_pdf(app, applicant, permit_type, barangay_name, city, out_path):
    """Generate a printable permit certificate PDF."""
    doc = SimpleDocTemplate(out_path, pagesize=letter, topMargin=0.9 * inch, bottomMargin=0.9 * inch)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CenterTitle", parent=styles["Title"], alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="CenterNormal", parent=styles["Normal"], alignment=TA_CENTER))

    elements = []
    elements.append(Paragraph("Republic of the Philippines", styles["CenterNormal"]))
    elements.append(Paragraph(city, styles["CenterNormal"]))
    elements.append(Paragraph(f"<b>{barangay_name}</b>", styles["CenterNormal"]))
    elements.append(Spacer(1, 18))
    elements.append(Paragraph(f"{permit_type.name.upper()}", styles["CenterTitle"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"Reference No: {app.reference_no}", styles["CenterNormal"]))
    elements.append(Spacer(1, 24))

    body = (
        f"This is to certify that <b>{applicant.full_name}</b>, of legal age, and a resident of "
        f"{applicant.address or '____________________'}, is hereby granted this "
        f"<b>{permit_type.name}</b> for the purpose of: <i>{app.purpose or 'N/A'}</i>."
    )
    elements.append(Paragraph(body, styles["Normal"]))
    elements.append(Spacer(1, 18))

    data = [
        ["Reference No.", app.reference_no],
        ["Date Issued", app.decision_date.strftime("%B %d, %Y") if app.decision_date else "-"],
        ["Valid Until", "See barangay records" if not permit_type.validity_days else
            f"{permit_type.validity_days} days from issuance"],
        ["Status", app.status],
    ]
    t = Table(data, colWidths=[2 * inch, 3.5 * inch])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 48))

    sig_data = [["_______________________", "_______________________"],
                ["Applicant's Signature", "Barangay Captain / Authorized Official"]]
    sig = Table(sig_data, colWidths=[2.75 * inch, 2.75 * inch])
    sig.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
    ]))
    elements.append(sig)

    doc.build(elements)
    return out_path


def generate_report_pdf(title, headers, rows, out_path, subtitle=None):
    """Generate a generic tabular report PDF (used for permit / blotter reports)."""
    doc = SimpleDocTemplate(out_path, pagesize=letter, topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    styles = getSampleStyleSheet()
    elements = [Paragraph(title, styles["Title"])]
    if subtitle:
        elements.append(Paragraph(subtitle, styles["Normal"]))
    elements.append(Spacer(1, 12))

    table_data = [headers] + rows
    t = Table(table_data, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B3A5C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F5F8")]),
    ]))
    elements.append(t)
    doc.build(elements)
    return out_path


def generate_submission_receipt(record, submission_type, processing_time,
                                barangay_name="Barangay Minante 1",
                                location="Cauayan City, Isabela"):
    """Generate a one-page acknowledgment from an already-persisted record."""
    kind=submission_type.lower()
    if kind not in {"permit","event","blotter"}: raise ValueError("Unsupported receipt type")
    if kind=="permit": owner=record.applicant; submitted=record.application_date
    elif kind=="event": owner=record.requester; submitted=record.created_at
    else: owner=record.complainant; submitted=record.filed_date
    reference=record.reference_no if kind!="blotter" else record.case_no
    submitted=submitted or record.created_at
    if kind=="permit":
        details=[("Permit Type",record.permit_type.name),("Purpose",record.purpose or "Not specified"),("What to Bring",record.permit_type.requirements or "Valid government-issued ID, this receipt, and original supporting documents when requested.")]
    elif kind=="event":
        details=[("Event Name",record.event_name),("Event Type",record.event_type),("Proposed Date",record.proposed_date.strftime("%B %d, %Y")),("Proposed Location",record.proposed_location),("What to Bring","Valid government-issued ID, this receipt, and original supporting documents required for the event review.")]
    else:
        details=[("Incident Type",record.incident_type),("Incident Date",record.incident_date.strftime("%B %d, %Y")),("What to Bring","A valid ID and any original supporting evidence or documents requested by the Barangay.")]
    label={"permit":"Permit Application","event":"Event Approval Request","blotter":"Blotter Report / Complaint"}[kind]
    buffer=BytesIO(); navy=colors.HexColor("#082F5B"); gold=colors.HexColor("#D4A62A"); pale=colors.HexColor("#F4F7FB")
    doc=SimpleDocTemplate(buffer,pagesize=letter,rightMargin=.55*inch,leftMargin=.55*inch,topMargin=.42*inch,bottomMargin=.38*inch,pageCompression=0,
                          title=f"Submission Receipt {reference}",author=barangay_name)
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReceiptBrand",parent=styles["Heading2"],fontName="Helvetica-Bold",fontSize=13,textColor=navy,alignment=TA_CENTER,spaceAfter=2))
    styles.add(ParagraphStyle(name="ReceiptTitle",parent=styles["Title"],fontName="Helvetica-Bold",fontSize=16,textColor=navy,alignment=TA_CENTER,spaceBefore=8,spaceAfter=4))
    styles.add(ParagraphStyle(name="ReceiptSmall",parent=styles["BodyText"],fontSize=8.4,leading=11,textColor=colors.HexColor("#334155")))
    styles.add(ParagraphStyle(name="ReceiptDisclaimer",parent=styles["BodyText"],fontName="Helvetica-Bold",fontSize=8.4,leading=11,textColor=colors.HexColor("#8B2F2F"),alignment=TA_CENTER))
    elements=[Paragraph("REPUBLIC OF THE PHILIPPINES",styles["ReceiptSmall"]),Paragraph(barangay_name.upper(),styles["ReceiptBrand"]),Paragraph(location,styles["ReceiptSmall"]),
              Paragraph("BARANGAY MINANTE 1 INTEGRATED MANAGEMENT SYSTEM",styles["ReceiptSmall"]),Paragraph("SUBMISSION ACKNOWLEDGMENT RECEIPT",styles["ReceiptTitle"])]
    refbox=Table([[Paragraph("REFERENCE NUMBER",styles["ReceiptSmall"]),Paragraph(f"<b>{reference}</b>",styles["ReceiptBrand"])]],colWidths=[1.6*inch,5.15*inch])
    refbox.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),pale),("BOX",(0,0),(-1,-1),1,gold),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)])); elements += [refbox,Spacer(1,8)]
    info=[("Submission Type",label),("Resident / Applicant",owner.full_name),("Date Submitted",submitted.strftime("%B %d, %Y")),("Time Submitted",submitted.strftime("%I:%M %p")),("Current Status","Received / Pending Review")]+details
    table=Table([[Paragraph(f"<b>{k}</b>",styles["ReceiptSmall"]),Paragraph(str(v),styles["ReceiptSmall"])] for k,v in info],colWidths=[1.65*inch,5.1*inch])
    table.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.35,colors.HexColor("#CBD5E1")),("BACKGROUND",(0,0),(0,-1),pale),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)])); elements += [table,Spacer(1,7)]
    confirmation=f"Your submission was successfully received by {barangay_name} on {submitted.strftime('%B %d, %Y')} at {submitted.strftime('%I:%M %p')}. Please keep this acknowledgment and reference number for tracking and follow-up."
    elements += [Paragraph(f"<b>RECEIVED CONFIRMATION</b><br/>{confirmation}",styles["ReceiptSmall"]),Spacer(1,6),
                 Paragraph(f"<b>EXPECTED PROCESSING TIME: {processing_time}</b><br/>Estimated processing time is subject to document completeness, verification, and Barangay review. Approval is not guaranteed.",styles["ReceiptSmall"]),Spacer(1,6),
                 Paragraph("<b>NEXT STEPS</b><br/>1. Barangay Staff will review the submission.<br/>2. Additional requirements may be requested when necessary.<br/>3. Status updates will be sent through available notification channels.<br/>4. Final approval or decision remains subject to the authorized Barangay Administrator.",styles["ReceiptSmall"]),Spacer(1,8),
                 Paragraph("This document confirms receipt of the submission only. It is not proof of approval, authorization, permit issuance, or final Barangay decision.",styles["ReceiptDisclaimer"]),Spacer(1,6),
                 Paragraph(f"Generated securely from Barangay records • {reference}",styles["ReceiptSmall"])]
    doc.build(elements); buffer.seek(0); return buffer
