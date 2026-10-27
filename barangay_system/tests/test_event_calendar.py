"""Calendar availability, privacy and the existing approval/reservation workflow."""
import unittest
from datetime import datetime, timedelta, time
import test_system as baseline
from app import app
from models import db, EventRequest, EventReservation, EventVenue
from services.event_availability import create_hold


class EventCalendarTests(unittest.TestCase):
    setUp=baseline.SystemAcceptanceTests.setUp
    auth=baseline.SystemAcceptanceTests.auth
    post=baseline.SystemAcceptanceTests.post
    future_event_data=baseline.SystemAcceptanceTests.future_event_data
    setUpClass=classmethod(lambda cls:app.config.update(TESTING=True))

    def event(self):
        self.auth('resident');payload=self.future_event_data(days=30,name='Private event title')
        self.assertEqual(self.post('/events',payload).status_code,302)
        with app.app_context():item=EventRequest.query.one();return item.id,payload

    def day(self,payload,venue=None,interval=True):
        query={'month':payload['proposed_date'][:7],'venue_id':venue or self.ids['venue']}
        if interval:query.update(start_time=payload['proposed_start_time'],end_time=payload['proposed_end_time'])
        response=self.client.get('/events/availability',query_string=query)
        self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response.headers['Cache-Control'])
        return next(day for day in response.json['days'] if day['date']==payload['proposed_date'])

    def test_available_partial_pending_and_multiple_venues(self):
        event_id,payload=self.event()
        day=self.day(payload)
        self.assertFalse(day['available']);self.assertEqual(day['state'],'pending')
        self.assertTrue(self.day(payload,interval=False)['available'])
        later=dict(payload,proposed_start_time='15:00',proposed_end_time='16:00')
        self.assertTrue(self.day(later)['available'])
        with app.app_context():
            venue=EventVenue(name='Another venue',address='Barangay');db.session.add(venue);db.session.commit();venue_id=venue.id
        self.assertTrue(self.day(payload,venue=venue_id)['available'])
        for role in ['admin','staff','resident']:
            self.auth(role)
            self.assertIn(b'Event Availability Calendar',self.client.get('/events').data)

    def test_privacy_and_all_application_statuses(self):
        event_id,payload=self.event()
        self.auth('other')
        response=self.client.get('/events/availability',query_string={'month':payload['proposed_date'][:7],'venue_id':self.ids['venue']})
        for private in [b'Private event title',b'Resident One',b'requester_id',b'hold_token',b'draft_payload']:
            self.assertNotIn(private,response.data)
        for status in ['Pending','Under Review','Endorsed to Admin','Approved','Rejected','Cancelled','Completed']:
            with self.subTest(status=status):
                with app.app_context():item=db.session.get(EventRequest,event_id);item.status=status;db.session.commit()
                day=self.day(payload)
                self.assertEqual(day['available'],status in {'Rejected','Cancelled','Completed'})
                if status=='Approved':self.assertEqual(day['state'],'booked')
        for role in ['admin','staff','resident']:
            self.auth(role)
            self.assertTrue(any(item.get('name')=='Private event title' for item in self.day(payload)['details']))

    def test_create_approve_reschedule_cancel_and_conflict_revalidation(self):
        event_id,payload=self.event()
        self.auth('staff')
        self.assertEqual(self.post(f'/events/{event_id}',{'status':'Under Review'},follow_redirects=True).status_code,200)
        self.assertEqual(self.post(f'/events/{event_id}',{'status':'Endorsed to Admin'},follow_redirects=True).status_code,200)
        self.auth('admin')
        self.assertEqual(self.post(f'/events/{event_id}',{'status':'Approved'},follow_redirects=True).status_code,200)
        self.assertEqual(self.day(payload)['state'],'booked')
        self.auth('other')
        self.assertEqual(self.post('/events',payload).status_code,200)  # Existing private alternative-slot flow.
        with app.app_context():self.assertEqual(EventRequest.query.count(),1)
        self.auth('staff')
        self.assertEqual(self.post(f'/events/{event_id}/reschedule',payload).status_code,403)
        moved=dict(payload,proposed_start_time='17:00',proposed_end_time='18:00')
        self.auth('admin')
        self.assertEqual(self.post(f'/events/{event_id}/reschedule',moved).status_code,302)
        self.assertTrue(self.day(payload)['available'])
        self.assertEqual(self.day(moved)['state'],'booked')
        with app.app_context():
            item=db.session.get(EventRequest,event_id)
            self.assertEqual(item.status,'Approved')
            self.assertEqual(item.reservation.starts_at.time(),time(17))
        self.assertEqual(self.post(f'/events/{event_id}',{'status':'Cancelled'},follow_redirects=True).status_code,200)
        self.assertTrue(self.day(moved)['available'])
        with app.app_context():self.assertEqual(db.session.get(EventRequest,event_id).reservation.status,'Released')

    def test_legacy_approved_event_blocks_calendar_and_new_requests(self):
        payload=self.future_event_data(days=30)
        self.auth('resident')
        with app.app_context():
            item=EventRequest(requester_id=self.ids['other'],event_name='Private legacy approved',
                description='Private legacy details',status='Approved',venue_id=self.ids['venue'],
                proposed_date=datetime.fromisoformat(payload['proposed_date']).date(),
                proposed_start_time=time(9),proposed_end_time=time(12))
            db.session.add(item);db.session.commit()
        self.assertEqual(self.day(payload)['state'],'booked')
        self.assertEqual(self.post('/events',payload).status_code,200)
        with app.app_context():self.assertEqual(EventRequest.query.count(),1)

    def test_active_and_expired_holds_and_invalid_parameters(self):
        self.auth('resident');payload=self.future_event_data(days=30)
        starts=datetime.fromisoformat(payload['proposed_date']+'T'+payload['proposed_start_time'])
        ends=datetime.fromisoformat(payload['proposed_date']+'T'+payload['proposed_end_time'])
        with app.app_context():
            token,hold=create_hold(self.ids['venue'],self.ids['other'],starts,ends,{'description':'Private draft'})
            db.session.commit();hold_id=hold.id
        day=self.day(payload);self.assertFalse(day['available'])
        self.assertEqual(day['details'][0]['status'],'Temporary hold')
        self.assertNotIn(token,str(day));self.assertNotIn('Private draft',str(day))
        with app.app_context():
            hold=db.session.get(EventReservation,hold_id);hold.hold_expires_at=datetime.utcnow()-timedelta(minutes=1);db.session.commit()
        self.assertTrue(self.day(payload)['available'])
        for query in [{'month':'bad','venue_id':self.ids['venue']},{'month':'9999-12','venue_id':self.ids['venue']},
                      {'venue_id':999999},{'venue_id':self.ids['venue'],'start_time':'13:00','end_time':'12:00'}]:
            self.assertEqual(self.client.get('/events/availability',query_string=query).status_code,400)
        with self.client.session_transaction() as session:session.clear()
        self.assertEqual(self.client.get('/events/availability').status_code,302)


if __name__=='__main__':unittest.main()
