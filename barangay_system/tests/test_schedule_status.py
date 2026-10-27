"""Schedule form choices, persistence, notifications and rejection regressions."""
import re
import unittest
from datetime import datetime

import test_system as baseline
from app import app
from models import db, Schedule, Notification, ActivityLog
from workflows import TRANSITIONS


class ScheduleStatusTests(unittest.TestCase):
    setUp = baseline.SystemAcceptanceTests.setUp
    auth = baseline.SystemAcceptanceTests.auth
    post = baseline.SystemAcceptanceTests.post
    create_permit = baseline.SystemAcceptanceTests.create_permit
    setUpClass = classmethod(lambda cls: app.config.update(TESTING=True))

    def schedule(self):
        permit_id, _ = self.create_permit()
        with app.app_context():
            item = Schedule(requested_by=self.ids['resident'], related_type='permit', related_id=permit_id,
                scheduled_datetime=datetime(2026,12,1,9), purpose='Fixture schedule')
            db.session.add(item); db.session.commit()
            return item.id

    def test_each_role_and_current_status_renders_only_allowed_options(self):
        schedule_id = self.schedule()
        for role in ['admin', 'staff', 'resident']:
            self.auth(role)
            for current in ['Requested','Confirmed','Rescheduled','Completed','Cancelled']:
                with self.subTest(role=role, current=current):
                    with app.app_context():
                        item = db.session.get(Schedule, schedule_id); item.status=current; db.session.commit()
                    page = self.client.get('/schedules')
                    self.assertEqual(page.status_code, 200)
                    form = re.search(rf'<form[^>]*action="/schedules/{schedule_id}/status"[^>]*>(.*?)</form>',
                                     page.data.decode(), re.S)
                    expected = TRANSITIONS['schedule'].get(role, {}).get(current, set())
                    if expected:
                        self.assertIsNotNone(form)
                        options = re.findall(r'<option value="([^"]+)"', form.group(1))
                        self.assertEqual(set(options), expected)
                        self.assertIn('name="csrf_token"', form.group(1))
                        self.assertIn('required', form.group(1))
                    else:
                        self.assertIsNone(form)

    def test_every_supported_transition_saves_and_survives_reload(self):
        schedule_id = self.schedule()
        for role, states in TRANSITIONS['schedule'].items():
            self.auth(role)
            for current, targets in states.items():
                for target in targets:
                    with self.subTest(role=role, current=current, target=target):
                        with app.app_context():
                            item = db.session.get(Schedule,schedule_id)
                            item.status=current; item.confirmed_by=None; db.session.commit()
                            previous_notifications = Notification.query.count()
                            reference = item.reference_no
                        response = self.post(f'/schedules/{schedule_id}/status', {'status':target})
                        self.assertEqual(response.status_code, 302)
                        self.assertEqual(response.location, '/schedules')
                        with app.app_context():
                            item = db.session.get(Schedule,schedule_id)
                            self.assertEqual(item.status,target)
                            self.assertEqual(item.confirmed_by,self.ids[role])
                            log = ActivityLog.query.filter_by(action='SCHEDULE_STATUS_CHANGED').order_by(ActivityLog.id.desc()).first()
                            self.assertEqual(log.details,f'{current} -> {target}')
                            self.assertEqual(log.user_id,self.ids[role])
                            self.assertGreater(Notification.query.count(),previous_notifications)
                            notice = Notification.query.filter_by(user_id=self.ids['resident'],channel='in_app').order_by(Notification.id.desc()).first()
                            self.assertEqual(notice.message,f'{reference}: {target}')
                        page = self.client.get('/schedules')
                        self.assertEqual(page.status_code,200)
                        self.assertIn(f'<td>{target}</td>'.encode(),page.data)
                        self.assertIn(f'Schedule status updated to {target}.'.encode(),page.data)

    def test_invalid_missing_repeated_and_forbidden_transitions_do_not_save(self):
        schedule_id = self.schedule()
        for role, current, target in [
            ('admin','Requested','Completed'), ('admin','Requested','Rescheduled'),
            ('staff','Requested','Cancelled'), ('staff','Confirmed','Cancelled'),
            ('admin','Confirmed','Confirmed'), ('admin','Completed','Confirmed'),
            ('admin','Cancelled','Rescheduled'), ('admin','Requested','confirmed'),
            ('admin','Requested','INVALID PRIVATE INPUT'), ('admin','Requested',''),
            ('staff','Rescheduled','Requested')]:
            with self.subTest(role=role,current=current,target=target):
                self.auth(role)
                with app.app_context():
                    item = db.session.get(Schedule,schedule_id);item.status=current;item.confirmed_by=None;db.session.commit()
                    counts = Notification.query.count(), ActivityLog.query.count()
                with self.assertLogs(app.logger, level='WARNING') as captured:
                    response = self.post(f'/schedules/{schedule_id}/status', {'status':target} if target else {})
                self.assertEqual(response.status_code,400)
                self.assertNotIn('INVALID PRIVATE INPUT',' '.join(captured.output))
                self.assertNotIn('Resident One',' '.join(captured.output))
                self.assertIn(b'Reload the Schedules page',response.data)
                with app.app_context():
                    item=db.session.get(Schedule,schedule_id)
                    self.assertEqual(item.status,current)
                    self.assertIsNone(item.confirmed_by)
                    self.assertEqual((Notification.query.count(),ActivityLog.query.count()),counts)

    def test_csrf_authorization_and_schedule_id_validation_are_preserved(self):
        schedule_id = self.schedule()
        self.auth('admin')
        for payload in [{'status':'Confirmed'}, {'status':'Confirmed','csrf_token':'wrong'}]:
            response=self.client.post(f'/schedules/{schedule_id}/status',data=payload)
            self.assertEqual(response.status_code,400)
            self.assertIn(b'Invalid or missing CSRF token',response.data)
        for record_id in ['999999','0','-1','invalid']:
            self.assertEqual(self.post(f'/schedules/{record_id}/status',{'status':'Confirmed'}).status_code,404)
        self.auth('resident')
        self.assertEqual(self.post(f'/schedules/{schedule_id}/status',{'status':'Confirmed'}).status_code,403)
        with app.app_context():
            self.assertEqual(db.session.get(Schedule,schedule_id).status,'Requested')
            self.assertEqual(Notification.query.count(),0)
            self.assertEqual(ActivityLog.query.count(),0)


if __name__ == '__main__': unittest.main()
