"""Privacy-safe SQL aggregates shared by dashboard, reports and PDF exports."""
from datetime import date, timedelta
from sqlalchemy import func, case, distinct
from models import (db, User, ResidentProfile, ResidentType, PermitApplication,
    PermitType, EventRequest, EventVenue, BlotterCase, QueueEntry,
    Schedule, Payment, Announcement,
    ResidentFeedback, ActivityLog, ApplicationRequirement, EventReservation, RequirementHistory)
from services.revision import local_datetime


def build_dashboard_analytics(moment=None):
    """Current service counts and twelve Manila submission months, without PII."""
    from datetime import datetime
    from services.revision import LOCAL_TIMEZONE

    local_now = (moment or datetime.now(LOCAL_TIMEZONE)).astimezone(LOCAL_TIMEZONE)
    current_month = local_now.year * 12 + local_now.month - 1
    months = [divmod(number, 12) for number in range(current_month - 11, current_month + 1)]
    month_keys = [f'{year:04}-{month + 1:02}' for year, month in months]
    start = local_datetime(month_keys[0] + '-01T00:00:00')
    end_year, end_month = divmod(current_month + 1, 12)
    end = local_datetime(f'{end_year:04}-{end_month + 1:02}-01T00:00:00')
    modules = []
    for key, label, model, total_label, metrics in [
        ('events', 'Event Requests', EventRequest, 'Total Event Requests',
         [('Pending Events', 'Pending'), ('Approved Events', 'Approved'),
          ('Rejected Events', 'Rejected'), ('Cancelled Events', 'Cancelled')]),
        ('permits', 'Permit Requests', PermitApplication, 'Total Permit Requests',
         [('Pending Permits', 'Pending'), ('Approved Permits', 'Approved'), ('Rejected Permits', 'Rejected')]),
        ('blotters', 'Blotter Records', BlotterCase, 'Total Blotter Records',
         [('Open / Active Cases', None), ('Resolved Cases', 'Resolved'), ('Closed Cases', 'Closed')]),
    ]:
        statuses = db.session.query(model.status, func.count(model.id)).group_by(model.status).order_by(model.status).all()
        counts = dict(statuses)
        total = sum(counts.values())
        cards = [{'label': total_label, 'value': total}]
        cards.extend({'label': title, 'value': counts.get(status, 0) if status else
                      sum(count for state, count in statuses if state not in {'Resolved', 'Closed'})}
                     for title, status in metrics)
        # Creation time matches reports: count submissions, not event or incident dates.
        month = func.strftime('%Y-%m', model.created_at, '+8 hours')
        recorded = dict(db.session.query(month, func.count(model.id)).filter(
            model.created_at >= start, model.created_at < end).group_by(month).all())
        trend = [[datetime(year, month + 1, 1).strftime('%b %Y'), recorded.get(month_key, 0)]
                 for (year, month), month_key in zip(months, month_keys)]
        charts = [
            {'id': key + '-status', 'title': label + ' by Status', 'kind': 'bar',
             'rows': [[state or 'Unspecified', count] for state, count in statuses]},
            {'id': key + '-monthly', 'title': 'Monthly ' + ('Blotter Report' if key == 'blotters' else label[:-1]) + ' Trend',
             'kind': 'line', 'rows': trend},
        ]
        if key == 'permits':
            rows = db.session.query(PermitType.name, func.count(PermitApplication.id)).select_from(PermitApplication).outerjoin(
                PermitType, PermitApplication.permit_type_id == PermitType.id).group_by(PermitType.id, PermitType.name).order_by(PermitType.name).all()
            if rows:
                charts.append({'id': 'permits-types', 'title': 'Permit Requests by Type', 'kind': 'bar',
                               'rows': [[name or 'Unspecified', count] for name, count in rows]})
        if key == 'blotters':
            category = func.coalesce(func.nullif(func.trim(BlotterCase.incident_type), ''), 'Unspecified')
            rows = db.session.query(category, func.count(BlotterCase.id)).group_by(category).order_by(category).all()
            if rows:
                charts.append({'id': 'blotters-categories', 'title': 'Blotter Records by Category', 'kind': 'bar',
                               'rows': [[name, count] for name, count in rows]})
        for chart in charts:
            chart['dashboard'] = True
            chart['has_data'] = any(row[1] for row in chart['rows'])
        modules.append({'key': key, 'label': label, 'heading':
                        {'events': 'Event Request Analytics', 'permits': 'Permit Request Analytics',
                         'blotters': 'Blotter Analytics'}[key], 'total': total, 'metrics': cards, 'charts': charts})
    return {'modules': modules, 'period': trend[0][0] + ' – ' + trend[-1][0]}

def population_sections():
    """Count each active approved resident profile once; never join family rows."""
    from services.resident_family import DISABILITY_TYPES
    query = db.session.query(User).join(ResidentProfile, ResidentProfile.user_id == User.id).filter(
        User.role.in_(('resident', 'staff')), User.is_active.is_(True),
        ResidentProfile.approval_status == 'Approved')
    gender = func.lower(func.trim(ResidentProfile.sex))
    def count_when(condition):
        return func.count(distinct(case((condition, User.id))))
    total, male, female, with_disability, without_disability = query.with_entities(
        func.count(distinct(User.id)), count_when(gender == 'male'), count_when(gender == 'female'),
        count_when(ResidentProfile.has_disability.is_(True)),
        count_when(ResidentProfile.has_disability.is_(False))).one()
    recorded_gender = male + female
    unknown_gender = total - recorded_gender
    unknown_disability = total - with_disability - without_disability
    kind = case(*[(func.lower(func.trim(ResidentProfile.disability_type)) == value.lower(), value)
                  for value in DISABILITY_TYPES], else_='Not recorded / unspecified')
    types = query.filter(ResidentProfile.has_disability.is_(True)).with_entities(
        kind, func.count(distinct(User.id))).group_by(kind).order_by(kind).all()
    return [
        {'title': 'Population Statistics', 'kind': 'table', 'rows': [
            ('Total Population', total), ('Male Residents', male), ('Female Residents', female),
            ('Residents with Disabilities', with_disability), ('Residents without Disabilities', without_disability),
            ('Gender not recorded / unspecified', unknown_gender),
            ('Disability status not recorded', unknown_disability)],
         'note': 'Unique active Resident/Staff records with an Approved resident profile, including approved offline residents. Family members without their own resident record are excluded.'},
        {'title': 'Gender Distribution', 'kind': 'donut', 'rows': [('Male', male), ('Female', female)],
         'percentages': {'Male': round(male * 100 / recorded_gender, 2) if recorded_gender else 0,
                         'Female': round(female * 100 / recorded_gender, 2) if recorded_gender else 0},
         'note': f'Percentages use {recorded_gender} residents with recorded Male/Female gender. {unknown_gender} missing or unrecognized gender values are excluded from this denominator.'},
        {'title': 'Disability Statistics', 'kind': 'donut',
         'rows': [('With disabilities', with_disability), ('Without disabilities', without_disability)],
         'note': f'{unknown_disability} residents have no recorded disability answer and are excluded from the Yes/No comparison.'},
        {'title': 'Disability Distribution by Type', 'kind': 'bar', 'rows': [(label, int(value)) for label, value in types],
         'note': 'Counts only residents whose disability answer is Yes. Missing or unrecognized types are grouped as Not recorded / unspecified. Individual details and free-text descriptions are excluded.'},
    ]


def build_scope_reports():
    """All-time core service reports, independent of request query/form values."""
    categories = []
    totals = []
    for key, title, model in [('permits', 'Permit Reports', PermitApplication),
                              ('events', 'Event Request Reports', EventRequest),
                              ('blotters', 'Blotter Reports', BlotterCase)]:
        data = build_report(validate_filters({'report_type': key}))
        total = db.session.query(func.count(model.id)).scalar()
        data['title'] = title
        data['sections'].insert(0, {'title': title + ' summary', 'kind': 'table',
                                    'rows': [('Total records', total)]})
        categories.append(data)
        totals.append((title, total))
    overall = {'title': 'Overall Barangay Report', 'sections': [
        {'title': 'Core service totals', 'kind': 'table',
         'rows': totals + [('Total core service records', sum(value for _, value in totals))]}]}
    overall['sections'].extend(population_sections())
    categories.insert(0, overall)
    return {'title': 'Overall Barangay Report', 'filters': {}, 'categories': categories,
            'sections': [section for category in categories for section in category['sections']],
            'unfiltered': True}


REPORT_TYPES = {'overall': 'Overall Barangay Report', 'residents': 'Resident Report',
    'permits': 'Permit Report', 'events': 'Event Report',
    'blotters': 'Blotter Report', 'walkin': 'Walk-in Transaction Report',
    'queue': 'Queue Report', 'appointments': 'Appointment Report', 'payments': 'Payment Report',
    'announcements': 'Announcement Report',
    'feedback': 'Feedback Report', 'staff': 'Staff Operational Report', 'audit': 'Activity / Audit Summary'}


def validate_filters(values):
    result = {key: values.get(key, '').strip() for key in
        ('report_type', 'start', 'end', 'year', 'month', 'status', 'source', 'service', 'purok', 'category')}
    result['report_type'] = result['report_type'] or 'overall'
    if result['report_type'] not in REPORT_TYPES:
        raise ValueError('Select a supported report type.')
    for key in ('start', 'end'):
        if result[key]:
            try:
                parsed = date.fromisoformat(result[key])
                if parsed.year >= 9999 or parsed.year < 1900:
                    raise ValueError()
            except ValueError:
                raise ValueError('Enter a valid report date.') from None
    if result['start'] and result['end'] and result['start'] > result['end']:
        raise ValueError('The report end date must follow the start date.')
    for key, lower, upper in [('year', 1900, 9998), ('month', 1, 12), ('service', 1, 2147483647)]:
        if result[key] and (not result[key].isdigit() or not lower <= int(result[key]) <= upper):
            raise ValueError('Invalid ' + key + ' filter.')
    if any(len(value) > 120 for value in result.values()):
        raise ValueError('Report filter is too long.')
    if result['source'] not in {'', 'Online', 'Walk-in / Staff Assisted'}:
        raise ValueError('Invalid transaction source.')
    applicable = {'status': {'permits','events','blotters','queue','appointments','announcements','payments'},
        'source': {'permits','walkin'}, 'service': {'permits','walkin'}, 'purok': {'residents','permits','walkin'},
        'category': {'announcements'}}
    for key, types in applicable.items():
        if result[key] and result['report_type'] not in types:
            raise ValueError(f'{key.title()} filter is not applicable to this report type.')
    return result


def build_report(filters):
    sections = []
    selected = filters['report_type']

    def query(model, timestamp=None, owner=None):
        q = db.session.query(model)
        stamp = timestamp if timestamp is not None else model.created_at
        if filters['start']:
            q = q.filter(stamp >= local_datetime(filters['start'] + 'T00:00:00'))
        if filters['end']:
            end = date.fromisoformat(filters['end']) + timedelta(days=1)
            q = q.filter(stamp < local_datetime(end.isoformat() + 'T00:00:00'))
        if filters['year']:
            q = q.filter(func.strftime('%Y', stamp, '+8 hours') == filters['year'])
        if filters['month']:
            q = q.filter(func.strftime('%m', stamp, '+8 hours') == f"{int(filters['month']):02d}")
        if filters['status'] and hasattr(model, 'status') and not (selected == 'payments' and model != Payment):
            q = q.filter(model.status == filters['status'])
        if filters['purok'] and owner is not None:
            q = q.join(ResidentProfile, ResidentProfile.user_id == owner).filter(ResidentProfile.purok_zone == filters['purok'])
        return q

    def add(title, q, column, kind='bar', amount=None):
        value = func.coalesce(func.sum(amount), 0) if amount is not None else func.count()
        rows = q.with_entities(column, value).group_by(column).order_by(column).all()
        sections.append({'title': title, 'kind': kind, 'rows': [(str(k) if k is not None else 'Unspecified', float(v) if amount is not None else int(v)) for k,v in rows]})

    def trend(title, q, stamp, amount=None):
        add(title, q, func.strftime('%Y-%m', stamp, '+8 hours'), 'line', amount)

    def summary(title, rows):
        sections.append({'title': title, 'kind': 'table', 'rows': rows})

    def stacked(title, q, stamp, status):
        month=func.strftime('%Y-%m',stamp,'+8 hours')
        rows=q.with_entities(month,status,func.count()).group_by(month,status).order_by(month,status).all()
        sections.append({'title':title,'kind':'stacked','rows':[(f'{m} · {s}',int(n)) for m,s,n in rows]})

    if selected in {'overall','residents'}:
        q = query(User).filter(User.role.in_(('resident','staff'))).join(ResidentProfile, ResidentProfile.user_id == User.id)
        if filters['purok']:
            q = q.filter(ResidentProfile.purok_zone == filters['purok'])
        summary('Resident population', [('Total registered Residents',q.count()), ('Active approved Residents',q.filter(User.is_active.is_(True),ResidentProfile.approval_status=='Approved').count())])
        add('Residency status',q,ResidentProfile.residency_status,'donut')
        add('Resident Type',q.outerjoin(ResidentType,ResidentProfile.resident_type_id==ResidentType.id),ResidentType.name,'donut')
        add('Residents by Purok',q,ResidentProfile.purok_zone)
        trend('Resident registrations',q,User.created_at)
    if selected in {'overall','permits','walkin'}:
        q = query(PermitApplication,owner=PermitApplication.applicant_id)
        if selected == 'walkin':
            q = q.filter(PermitApplication.request_source!='Online')
        if filters['source']:
            q = q.filter(PermitApplication.request_source==filters['source'])
        if filters['service']:
            q = q.filter(PermitApplication.permit_type_id==int(filters['service']))
        total = q.count()
        approved = q.filter(PermitApplication.status.in_(('Approved','Ready for Pickup','Completed'))).count()
        rejected = q.filter(PermitApplication.status=='Rejected').count()
        resubmitted = q.filter(PermitApplication.requirement_records.any(ApplicationRequirement.status=='Needs Resubmission')).count()
        summary('Permit service summary',[('Total requests',total),('Approval rate (%)',round(approved*100/total,2) if total else 0),('Rejection rate (%)',round(rejected*100/total,2) if total else 0),('Currently requiring resubmission (%)',round(resubmitted*100/total,2) if total else 0),('Documents released',q.filter(PermitApplication.released_at.isnot(None)).count())])
        add('Permit requests by status',q,PermitApplication.status,'donut')
        add('Requests by service',q.join(PermitType),PermitType.name)
        add('Online vs Walk-in',q,PermitApplication.request_source,'donut')
        trend('Monthly permit transactions',q,PermitApplication.created_at)
        stacked('Monthly request status composition',q,PermitApplication.created_at,PermitApplication.status)
        add('Requests by Purok',q.join(ResidentProfile,ResidentProfile.user_id==PermitApplication.applicant_id) if not filters['purok'] else q,ResidentProfile.purok_zone)
        historical_resubmissions=q.filter(PermitApplication.requirement_records.any(ApplicationRequirement.history.any(RequirementHistory.status=='Needs Resubmission'))).count()
        summary('Requirements resubmission history',[('Requests ever returned for requirements',historical_resubmissions),('Resubmission rate (%)',round(historical_resubmissions*100/total,2) if total else 0)])
        avg = q.filter(PermitApplication.released_at.isnot(None)).with_entities(func.avg((func.julianday(PermitApplication.released_at)-func.julianday(PermitApplication.created_at))*24)).scalar()
        if avg is not None:
            summary('Measured processing time',[('Average submission-to-release hours',round(avg,2))])
    if selected in {'overall','events'}:
        q=query(EventRequest)
        add('Event requests by status',q,EventRequest.status,'donut')
        add('Event requests by type',q,EventRequest.event_type)
        add('Event requests by venue',q,EventRequest.proposed_location)
        trend('Event requests over time',q,EventRequest.created_at)
        reservations=query(EventReservation).filter(EventReservation.status=='Reserved').join(EventRequest,EventReservation.event_request_id==EventRequest.id)
        add('Reserved venue hours',reservations.join(EventVenue,EventReservation.venue_id==EventVenue.id),EventVenue.name,
            amount=(func.julianday(EventReservation.ends_at)-func.julianday(EventReservation.starts_at))*24)
        summary('Event scheduling records',[('Schedule conflicts recorded',query(ActivityLog,ActivityLog.timestamp).filter(ActivityLog.action=='EVENT_SLOT_CONFLICT_DETECTED').count())])
    for key,model,title,column,owner in [('blotters',BlotterCase,'Blotter cases',BlotterCase.status,None),('queue',QueueEntry,'Queue volume',QueueEntry.status,None),('appointments',Schedule,'Appointments',Schedule.status,None)]:
        if selected in {'overall',key}:
            q=query(model,owner=owner)
            add(title,q,column)
            trend(title+' over time',q,model.created_at)
            if key=='queue':
                avg=q.filter(QueueEntry.called_at.isnot(None)).with_entities(func.avg((func.julianday(QueueEntry.called_at)-func.julianday(QueueEntry.created_at))*1440)).scalar()
                if avg is not None:
                    summary('Measured queue waiting time',[('Average waiting minutes',round(avg,2))])
                served=q.filter(QueueEntry.completed_at.isnot(None),QueueEntry.served_at.isnot(None)).with_entities(func.avg((func.julianday(QueueEntry.completed_at)-func.julianday(QueueEntry.served_at))*1440)).scalar()
                if served is not None:
                    summary('Measured queue serving time',[('Average serving minutes',round(served,2))])
    if selected in {'overall','payments'}:
        q=query(Payment,Payment.paid_at)
        paid=q.filter(Payment.status=='Paid')
        summary('Actual payment collections',[('Recorded payments',paid.count()),('Collected (PHP)',float(paid.with_entities(func.coalesce(func.sum(Payment.amount_paid),0)).scalar()))])
        trend('Payment collections over time (PHP)',paid,Payment.paid_at,Payment.amount_paid)
        add('Payments by service (PHP)',paid.join(PermitApplication,Payment.permit_id==PermitApplication.id).join(PermitType),PermitType.name,amount=Payment.amount_paid)
        add('Event collections (PHP)',paid.join(EventRequest,Payment.event_id==EventRequest.id),EventRequest.event_type,amount=Payment.amount_paid)
        add('Recorded payment decisions',q,Payment.status,'donut')
        unpaid_permits=query(PermitApplication).filter(PermitApplication.fee_at_submission>0,~PermitApplication.payment.has()).count()
        unpaid_events=query(EventRequest).filter(EventRequest.fee_at_submission>0,~EventRequest.payment.has()).count()
        summary('Unpaid requests (submission period)', [('Unpaid permits',unpaid_permits),('Unpaid events',unpaid_events)])
    if selected in {'overall','announcements'}:
        q=query(Announcement)
        if filters['category']:
            q=q.filter(Announcement.category==filters['category'])
        add('Announcement lifecycle',q,Announcement.status)
        add('Announcements by category',q,Announcement.category)
        trend('Announcements created by month',q,Announcement.created_at)
    if selected in {'overall','feedback'}:
        q=query(ResidentFeedback)
        add('Resident feedback ratings',q,ResidentFeedback.rating)
        average=q.with_entities(func.avg(ResidentFeedback.rating)).scalar()
        summary('Feedback summary',[('Responses',q.count()),('Average rating',round(average,2) if average is not None else 'No ratings')])
    if selected in {'overall','staff'}:
        q=query(ActivityLog,ActivityLog.timestamp).filter(ActivityLog.actor_role=='staff')
        add('Staff operational actions',q,ActivityLog.action)
        add('Staff assisted transactions',query(PermitApplication).filter(PermitApplication.encoded_by_staff_id.isnot(None)).join(User,PermitApplication.encoded_by_staff_id==User.id),User.full_name)
    if selected=='audit':
        q=query(ActivityLog,ActivityLog.timestamp)
        add('Audit actions',q,ActivityLog.action)
        add('Audit actions by role',q,ActivityLog.actor_role)
    return {'title':REPORT_TYPES[selected], 'filters':filters, 'sections':sections}


def chart_png(section):
    """Render aggregate data with a noninteractive plotting backend."""
    if not section['rows'] or section['kind']=='table':
        return None
    from io import BytesIO
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    rows=section['rows']
    labels=[r[0] for r in rows]
    values=[float(r[1]) for r in rows]
    if not any(values):
        return None
    figure=Figure(figsize=(7,3.8),layout='constrained')
    FigureCanvasAgg(figure)
    ax=figure.subplots()
    if section['kind']=='stacked':
        pairs=[label.split(' · ',1) for label in labels]
        months=list(dict.fromkeys(pair[0] for pair in pairs))
        statuses=list(dict.fromkeys(pair[1] for pair in pairs))
        lookup={tuple(pair):value for pair,value in zip(pairs,values)}
        bottom=[0]*len(months)
        palette=['#082F5B','#D4A62A','#52758F','#B7C9D6','#8D793E','#596677','#DDD3B6','#99A7AC']
        for index,status in enumerate(statuses):
            counts=[lookup.get((month,status),0) for month in months]
            ax.bar(months,counts,bottom=bottom,label=status,color=palette[index%len(palette)])
            bottom=[a+b for a,b in zip(bottom,counts)]
        ax.legend(fontsize=7,loc='upper left',bbox_to_anchor=(1,1))
        ax.tick_params(axis='x',rotation=35)
    elif section['kind']=='donut' and len(rows)<=8:
        ax.pie(values,labels=labels,colors=['#082F5B','#D4A62A','#52758F','#B7C9D6','#8D793E','#596677','#DDD3B6','#99A7AC'],wedgeprops={'width':0.45},autopct='%1.0f%%')
    elif section['kind']=='line':
        ax.plot(labels,values,color='#082F5B',marker='o')
        ax.tick_params(axis='x',rotation=35)
        ax.set_ylim(bottom=0)
    else:
        ax.barh(labels,values,color='#082F5B')
        ax.invert_yaxis()
    ax.set_title(section['title'])
    output=BytesIO()
    figure.savefig(output,format='png',dpi=120)
    output.seek(0)
    return output


def pdf_report(report, generated_by):
    """Print-ready report with editing/copying restrictions for compliant viewers.

    PDF permissions discourage changes; they are not a cryptographic signature.
    """
    import secrets
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pdfencrypt import StandardEncryption
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from matplotlib import font_manager
    from models import utcnow
    from services.revision import local_display

    font_name = 'BarangayReport'
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, font_manager.findfont('DejaVu Sans')))
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font_name
    styles['Normal'].fontSize = 9
    styles['Normal'].leading = 13
    styles['Heading2'].fontSize = 13
    output = BytesIO()
    permissions = StandardEncryption('', ownerPassword=secrets.token_urlsafe(48),
        canPrint=1, canModify=0, canCopy=0, canAnnotate=0, strength=128)
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=40, rightMargin=40,
        topMargin=40, bottomMargin=40, title=report['title'], author='Barangay Minante 1', encrypt=permissions)
    para = lambda value, style='Normal': Paragraph(escape(str(value)), styles[style])
    story = [para('Barangay Minante 1', 'Title'), para(report['title'], 'Heading1'),
        para('Generated by ' + generated_by + ' | ' + local_display(utcnow()) + ' (Asia/Manila)'),
        Spacer(1, 10), para('Active approved resident population, gender and disability aggregates, plus all recorded permit requests, event requests and blotter records. '
            'Personal applicant documents and case narratives are excluded.')]
    for section in report['sections']:
        story.append(para(section['title'], 'Heading2'))
        if section.get('note'):
            story.append(para(section['note']))
        chart = chart_png(section)
        if chart:
            story.append(Image(chart, width=document.width, height=document.width * 3.8 / 7))
        if not section['rows']:
            story.append(para('No records available.'))
            continue
        rows = [[para('Category / Metric'), para('Value')]]
        rows.extend([para(label), para(f"{value} ({section['percentages'][label]:.2f}%)"
                    if label in section.get('percentages', {}) else value)] for label, value in section['rows'])
        table = Table(rows, colWidths=[document.width * .76, document.width * .24], repeatRows=1,
                      hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor('#E8EEF5')),
            ('GRID', (0,0), (-1,-1), .4, colors.HexColor('#CCD5DF')),
            ('VALIGN', (0,0), (-1,-1), 'TOP'), ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6)]))
        story.extend([table, Spacer(1, 10)])

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.drawRightString(A4[0]-40, 22, f'Barangay Minante 1 | Page {doc.page}')
        canvas.restoreState()
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    output.seek(0)
    return output


