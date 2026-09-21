"""Acceptance and regression tests for the Barangay Minante 1 system.

The suite uses an isolated SQLite file. SQLite is intentionally used only as
the deterministic test backend; production database selection is exercised by
configuration and deployment checks outside this module.
"""

import os
import re
import tempfile
import unittest
import zipfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO


DB_FILE = os.path.join(
    tempfile.gettempdir(), f"barangay_system_tests_{os.getpid()}.db"
)
TEST_DATA_DIR = os.path.join(tempfile.gettempdir(), f"barangay_system_data_{os.getpid()}")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

os.environ["DATABASE_URL"] = "sqlite:///" + DB_FILE.replace("\\", "/")
os.environ["DATA_DIR"] = TEST_DATA_DIR
os.environ["SECRET_KEY"] = "acceptance-test-secret"

from app import app  # noqa: E402
from migration import upgrade_legacy_schema  # noqa: E402
from models import (  # noqa: E402
    AcceptedIdType,
    ActivityLog,
    BlotterCase,
    EventCategory,
    EventRequest,
    EventReservation,
    EventVenue,
    Notification,
    PasswordResetToken,
    PermitApplication,
    PermitType,
    Report,
    ResidentProfile,
    ResidentType,
    Schedule,
    User,
    db,
)
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402


DOCX_MIMETYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


class SystemAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.create_all()

            resident_type = ResidentType(
                name="Permanent Resident", description="Verified resident"
            )
            accepted_id = AcceptedIdType(
                name="Philippine National ID / PhilSys ID",
                description="Government ID",
                is_accepted_for_registration=True,
            )
            permit_type = PermitType(
                name="Barangay Clearance",
                description="General clearance",
                requirements="Valid ID",
                fee=Decimal("50.00"),
                fee_is_configured=True,
            )
            event_category = EventCategory(
                name="Community Activity",
                description="Community event",
                fee=Decimal("250.00"),
                fee_is_configured=True,
            )
            venue = EventVenue(
                name="Minante Covered Court",
                address="Barangay Minante 1",
                description="Public covered court",
            )
            db.session.add_all(
                [resident_type, accepted_id, permit_type, event_category, venue]
            )
            db.session.flush()

            self.ids = {
                "resident_type": resident_type.id,
                "accepted_id": accepted_id.id,
                "permit_type": permit_type.id,
                "event_category": event_category.id,
                "venue": venue.id,
            }
            users = (
                ("admin", "adminx", "admin", "admin@test.local", "Administrator One"),
                ("staff", "staffx", "staff", "staff@test.local", "Staff Member"),
                ("resident", "resx", "resident", "resident@test.local", "Resident One"),
                ("other", "resy", "resident", "other@test.local", "Resident Two"),
            )
            for key, username, role, email, full_name in users:
                user = User(
                    username=username,
                    role=role,
                    email=email,
                    full_name=full_name,
                    contact_number="09171234567",
                    address="House 1, Zone 1",
                )
                user.set_password("StrongPass123")
                db.session.add(user)
                db.session.flush()
                self.ids[key] = user.id
                if role in {"resident", "staff"}:
                    db.session.add(
                        ResidentProfile(
                            user_id=user.id,
                            date_of_birth=date(1990, 1, 1),
                            sex="Male",
                            civil_status="Single",
                            house_number_street="House 1",
                            purok_zone="Zone 1",
                            resident_type_id=resident_type.id,
                            years_of_residency=10,
                            occupation="Public service",
                            household_number="HH-001",
                            emergency_contact_name="Emergency Contact",
                            emergency_contact_relationship="Relative",
                            emergency_contact_number="09181234567",
                            accepted_id_type_id=accepted_id.id,
                            valid_id_number="ID-001",
                            valid_id_file_path="legacy-id.pdf",
                            id_verification_status="Verified",
                            address_verification_status="Verified",
                            privacy_consent=True,
                            privacy_consented_at=datetime.utcnow(),
                            approval_status="Approved",
                            reviewed_by=self.ids["admin"],
                            reviewed_at=datetime.utcnow(),
                        )
                    )
            db.session.commit()

    def auth(self, identity):
        user_id = self.ids[identity] if isinstance(identity, str) else identity
        with self.client.session_transaction() as session:
            session.clear()
            session["user_id"] = user_id
            session["csrf_token"] = "test-csrf-token"

    def clear_auth(self):
        with self.client.session_transaction() as session:
            session.clear()
            session["csrf_token"] = "test-csrf-token"

    def post(self, url, data=None, *, follow_redirects=False):
        with self.client.session_transaction() as session:
            session.setdefault("csrf_token", "test-csrf-token")
            csrf_token = session["csrf_token"]
        payload = dict(data or {})
        payload.setdefault("csrf_token", csrf_token)
        return self.client.post(url, data=payload, follow_redirects=follow_redirects)

    def registration_data(self, username="newresident", email="new@test.local"):
        return {
            "full_name": "New Resident",
            "date_of_birth": "1995-05-10",
            "sex": "Female",
            "civil_status": "Single",
            "contact_number": "09195551234",
            "email": email,
            "house_number_street": "House 22 Rizal Street",
            "purok_zone": "Purok 3",
            "resident_type_id": str(self.ids["resident_type"]),
            "residency_status": "Permanent Resident",
            "years_of_residency": "8",
            "occupation": "Teacher",
            "is_household_head": "1",
            "household_number": "HH-2026-022",
            "emergency_contact_name": "Maria Resident",
            "emergency_contact_relationship": "Mother",
            "emergency_contact_number": "09196662345",
            "accepted_id_type_id": str(self.ids["accepted_id"]),
            "valid_id_number": "NID-12345",
            "valid_id_file": (BytesIO(b"%PDF-1.4\nvalidation id"), "valid-id.pdf"),
            "username": username,
            "password": "Register123",
            "confirm_password": "Register123",
            "privacy_consent": "1",
        }

    def future_event_data(
        self,
        *,
        days=30,
        start="09:00",
        end="11:00",
        name="Community Assembly",
    ):
        event_date = date.today() + timedelta(days=days)
        return {
            "event_name": name,
            "category_id": str(self.ids["event_category"]),
            "venue_id": str(self.ids["venue"]),
            "description": "A real community activity request.",
            "proposed_date": event_date.isoformat(),
            "proposed_start_time": start,
            "proposed_end_time": end,
        }

    @staticmethod
    def docx_xml(response):
        with zipfile.ZipFile(BytesIO(response.data)) as archive:
            return archive.read("word/document.xml").decode("utf-8")

    def create_permit(self, owner="resident", **values):
        with app.app_context():
            item = PermitApplication(
                applicant_id=self.ids[owner],
                permit_type_id=self.ids["permit_type"],
                purpose=values.get("purpose", "Barangay transaction"),
                fee_at_submission=values.get("fee", Decimal("50.00")),
                status=values.get("status", "Pending"),
            )
            db.session.add(item)
            db.session.commit()
            return item.id, item.reference_no

    def create_conflict_and_hold(self):
        self.auth("resident")
        primary = self.post("/events", self.future_event_data(name="Reserved Event"))
        self.assertEqual(primary.status_code, 302)
        self.auth("other")
        recommendation = self.post(
            "/events", self.future_event_data(name="Conflicting Event")
        )
        self.assertEqual(recommendation.status_code, 200)
        match = re.search(
            rb'/events/recommendations/([^/"?]+)/accept', recommendation.data
        )
        self.assertIsNotNone(match, recommendation.data[:500])
        return match.group(1).decode("ascii"), recommendation

    def test_registration_is_pending_complete_and_gated_until_admin_approval(self):
        self.clear_auth()
        response = self.post("/register", self.registration_data())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))

        with app.app_context():
            user = User.query.filter_by(username="newresident").one()
            profile = user.resident_profile
            new_user_id = user.id
            self.assertTrue(user.check_password("Register123"))
            self.assertNotEqual(user.password_hash, "Register123")
            self.assertEqual(profile.approval_status, "Pending")
            self.assertEqual(profile.house_number_street, "House 22 Rizal Street")
            self.assertEqual(profile.purok_zone, "Purok 3")
            self.assertEqual(profile.years_of_residency, 8)
            self.assertTrue(profile.is_household_head)
            self.assertEqual(profile.household_number, "HH-2026-022")
            self.assertEqual(profile.emergency_contact_name, "Maria Resident")
            self.assertEqual(profile.emergency_contact_relationship, "Mother")
            self.assertEqual(profile.emergency_contact_number, "09196662345")
            self.assertEqual(profile.valid_id_number, "NID-12345")
            self.assertTrue(profile.privacy_consent)
            self.assertIsNotNone(profile.privacy_consented_at)
            self.assertTrue(
                ActivityLog.query.filter_by(
                    user_id=user.id, action="RESIDENT_REGISTERED"
                ).count()
            )

        self.clear_auth()
        pending = self.post(
            "/login",
            {"username": "newresident", "password": "Register123"},
            follow_redirects=True,
        )
        self.assertEqual(pending.status_code, 200)
        self.assertIn(b"still being verified", pending.data)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

        self.auth("admin")
        self.assertEqual(
            self.post(f"/users/{new_user_id}/verification/id", {"action": "verify"}).status_code,
            302,
        )
        self.assertEqual(
            self.post(
                f"/users/{new_user_id}/verification/address", {"action": "verify"}
            ).status_code,
            302,
        )
        approved = self.post(
            f"/users/{new_user_id}/approval",
            {"action": "approve", "remarks": "Identity verified."},
        )
        self.assertEqual(approved.status_code, 302)
        with app.app_context():
            profile = db.session.get(User, new_user_id).resident_profile
            self.assertEqual(profile.approval_status, "Approved")
            self.assertEqual(profile.reviewed_by, self.ids["admin"])
            self.assertIsNotNone(profile.reviewed_at)
            self.assertTrue(
                ActivityLog.query.filter_by(
                    action="ACCOUNT_APPROVED", target_reference=str(new_user_id)
                ).count()
            )

        self.clear_auth()
        login = self.post(
            "/login", {"username": "newresident", "password": "Register123"}
        )
        self.assertEqual(login.status_code, 302)
        self.assertTrue(login.headers["Location"].endswith("/dashboard"))
        with self.client.session_transaction() as session:
            self.assertEqual(session["user_id"], new_user_id)

    def test_rejected_registration_cannot_login_and_consent_is_required(self):
        self.clear_auth()
        without_consent = self.registration_data("noconsent", "no@test.local")
        without_consent.pop("privacy_consent")
        response = self.post("/register", without_consent, follow_redirects=True)
        self.assertIn(b"agree to the Privacy Notice", response.data)
        with app.app_context():
            self.assertIsNone(User.query.filter_by(username="noconsent").first())

        response = self.post(
            "/register", self.registration_data("rejected", "reject@test.local")
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            rejected_id = User.query.filter_by(username="rejected").one().id

        self.auth("admin")
        response = self.post(
            f"/users/{rejected_id}/approval",
            {"action": "reject", "remarks": "Verification failed."},
        )
        self.assertEqual(response.status_code, 302)
        self.clear_auth()
        response = self.post(
            "/login",
            {"username": "rejected", "password": "Register123"},
            follow_redirects=True,
        )
        self.assertIn(b"registration was not approved", response.data)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_resident_type_and_residency_status_are_separate_and_others_require_details(self):
        with app.app_context():
            resident_other = ResidentType(name="Others", is_active=True)
            id_other = AcceptedIdType(
                name="Others", is_active=True, is_accepted_for_registration=True
            )
            db.session.add_all([resident_other, id_other])
            db.session.commit()
            resident_other_id, id_other_id = resident_other.id, id_other.id
        self.clear_auth()
        invalid = self.registration_data("otherstype", "otherstype@test.local")
        invalid.update(
            {"resident_type_id": resident_other_id, "accepted_id_type_id": id_other_id}
        )
        response = self.post("/register", invalid, follow_redirects=True)
        self.assertIn(b"Specify the Resident Type", response.data)
        valid = self.registration_data("otherstype2", "otherstype2@test.local")
        valid.update(
            {
                "resident_type_id": resident_other_id,
                "resident_type_other": "Caretaker for private household",
                "accepted_id_type_id": id_other_id,
                "valid_id_type_other": "Local Barangay identification",
                "residency_status": "Permanent Resident",
                "valid_id_file": (BytesIO(b"%PDF-1.4\nsecond id"), "id.pdf"),
            }
        )
        self.assertEqual(self.post("/register", valid).status_code, 302)
        with app.app_context():
            profile = User.query.filter_by(username="otherstype2").one().resident_profile
            self.assertEqual(profile.resident_type.name, "Others")
            self.assertEqual(profile.resident_type_other, "Caretaker for private household")
            self.assertEqual(profile.residency_status, "Permanent Resident")
            self.assertEqual(profile.valid_id_type_other, "Local Barangay identification")

    def test_id_document_is_private_and_account_approval_requires_both_verifications(self):
        self.clear_auth()
        self.assertEqual(self.post("/register", self.registration_data("verifyme", "verify@test.local")).status_code, 302)
        with app.app_context():
            user_id = User.query.filter_by(username="verifyme").one().id
        self.auth("admin")
        blocked = self.post(f"/users/{user_id}/approval", {"action": "approve"}, follow_redirects=True)
        self.assertIn(b"cannot be approved", blocked.data)
        self.auth("other")
        self.assertEqual(self.client.get(f"/resident-identifications/{user_id}/document").status_code, 403)
        self.auth("admin")
        self.assertEqual(self.client.get(f"/resident-identifications/{user_id}/document").status_code, 200)
        self.assertEqual(self.post(f"/users/{user_id}/verification/id", {"action": "verify"}).status_code, 302)
        self.assertEqual(self.post(f"/users/{user_id}/verification/address", {"action": "verify"}).status_code, 302)
        self.assertEqual(self.post(f"/users/{user_id}/approval", {"action": "approve"}).status_code, 302)

    def test_configured_fees_are_snapshotted_and_unconfigured_services_are_blocked(self):
        self.auth("resident")
        permit = self.post(
            "/permits",
            {
                "permit_type_id": self.ids["permit_type"],
                "purpose": "Employment requirement",
            },
        )
        self.assertEqual(permit.status_code, 302)
        self.assertIn("/submissions/permit/", permit.headers["Location"])
        event = self.post("/events", self.future_event_data())
        self.assertEqual(event.status_code, 302)
        self.assertIn("/submissions/event/", event.headers["Location"])

        with app.app_context():
            saved_permit = PermitApplication.query.one()
            saved_event = EventRequest.query.one()
            self.assertEqual(saved_permit.fee_at_submission, Decimal("50.00"))
            self.assertEqual(saved_event.fee_at_submission, Decimal("250.00"))
            self.assertEqual(saved_event.expected_attendees, 0)
            self.assertIsNone(saved_event.supporting_document_path)
            permit_type = db.session.get(PermitType, self.ids["permit_type"])
            category = db.session.get(EventCategory, self.ids["event_category"])
            permit_type.fee = Decimal("75.00")
            category.fee = Decimal("300.00")
            db.session.commit()
            self.assertEqual(saved_permit.fee_at_submission, Decimal("50.00"))
            self.assertEqual(saved_event.fee_at_submission, Decimal("250.00"))
            permit_type.fee_is_configured = False
            db.session.commit()
            before = PermitApplication.query.count()

        blocked = self.post(
            "/permits",
            {
                "permit_type_id": self.ids["permit_type"],
                "purpose": "Should not save",
            },
            follow_redirects=True,
        )
        self.assertIn(b"unavailable until its fee is configured", blocked.data)
        with app.app_context():
            self.assertEqual(PermitApplication.query.count(), before)

    def test_blotter_respondent_is_required_saved_and_private(self):
        self.auth("resident")
        incident = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        incomplete = self.post(
            "/blotters",
            {
                "respondent_name": "",
                "incident_type": "Noise complaint",
                "incident_date": incident,
                "incident_location": "Purok 3",
                "narrative": "Incident narrative",
            },
        )
        self.assertEqual(incomplete.status_code, 200)
        with app.app_context():
            self.assertEqual(BlotterCase.query.count(), 0)

        created = self.post(
            "/blotters",
            {
                "respondent_name": "Juan Dela Cruz",
                "respondent_address": "Purok 4",
                "incident_type": "Noise complaint",
                "incident_date": incident,
                "incident_location": "Purok 3",
                "narrative": "A private and factual incident narrative.",
            },
        )
        self.assertEqual(created.status_code, 302)
        with app.app_context():
            item = BlotterCase.query.one()
            blotter_id = item.id
            self.assertEqual(item.respondent_name, "Juan Dela Cruz")
            self.assertEqual(item.respondent_address, "Purok 4")

        detail = self.client.get(f"/blotters/{blotter_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Juan Dela Cruz", detail.data)
        self.auth("other")
        self.assertEqual(self.client.get(f"/blotters/{blotter_id}").status_code, 403)

    def test_staff_can_submit_personal_permit_and_event_but_cannot_process_them(self):
        self.auth("staff")
        page = self.client.get("/permits?scope=mine")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Start a new permit application", page.data)
        permit_response = self.post(
            "/permits?scope=mine",
            {
                "permit_type_id": self.ids["permit_type"],
                "purpose": "Staff personal clearance",
            },
        )
        self.assertEqual(permit_response.status_code, 302)
        event_response = self.post(
            "/events?scope=mine",
            self.future_event_data(days=40, name="Staff Family Event"),
        )
        self.assertEqual(event_response.status_code, 302)

        with app.app_context():
            permit = PermitApplication.query.filter_by(
                applicant_id=self.ids["staff"]
            ).one()
            event = EventRequest.query.filter_by(requester_id=self.ids["staff"]).one()
            permit_id, event_id = permit.id, event.id
            self.assertGreaterEqual(
                ActivityLog.query.filter_by(
                    user_id=self.ids["staff"],
                    action="STAFF_PERSONAL_REQUEST_SUBMITTED",
                ).count(),
                2,
            )

        self.assertEqual(
            self.post(f"/permits/{permit_id}", {"status": "Under Review"}).status_code,
            403,
        )
        self.assertEqual(
            self.post(f"/events/{event_id}", {"status": "Under Review"}).status_code,
            403,
        )
        with app.app_context():
            self.assertEqual(db.session.get(PermitApplication, permit_id).status, "Pending")
            self.assertEqual(db.session.get(EventRequest, event_id).status, "Pending")

    def test_overlapping_event_creates_private_ten_minute_hold_and_accepts_it(self):
        token, recommendation = self.create_conflict_and_hold()
        self.assertNotIn(b"Resident One", recommendation.data)
        self.assertNotIn(b"resx", recommendation.data)
        self.assertNotIn(b"Reserved Event", recommendation.data)
        self.assertIn(b"10-minute", recommendation.data)

        with app.app_context():
            hold = EventReservation.query.filter_by(status="Held").one()
            hold_id = hold.id
            held_start = hold.starts_at
            self.assertEqual(hold.requester_id, self.ids["other"])
            remaining = hold.hold_expires_at - datetime.utcnow()
            self.assertGreater(remaining, timedelta(minutes=9))
            self.assertLessEqual(remaining, timedelta(minutes=10, seconds=5))
            self.assertEqual(held_start.date(), date.today() + timedelta(days=31))

        self.auth("resident")
        wrong_owner = self.post(f"/events/recommendations/{token}/accept")
        self.assertEqual(wrong_owner.status_code, 302)
        with app.app_context():
            self.assertEqual(db.session.get(EventReservation, hold_id).status, "Held")

        self.auth("other")
        accepted = self.post(f"/events/recommendations/{token}/accept")
        self.assertEqual(accepted.status_code, 302)
        self.assertIn("/submissions/event/", accepted.headers["Location"])
        with app.app_context():
            hold = db.session.get(EventReservation, hold_id)
            self.assertEqual(hold.status, "Reserved")
            self.assertIsNotNone(hold.event_request_id)
            self.assertIsNone(hold.hold_expires_at)
            self.assertIsNone(hold.hold_token_hash)
            accepted_event = db.session.get(EventRequest, hold.event_request_id)
            self.assertEqual(accepted_event.requester_id, self.ids["other"])
            self.assertEqual(accepted_event.proposed_date, held_start.date())
            self.assertEqual(accepted_event.fee_at_submission, Decimal("250.00"))

    def test_event_hold_decline_releases_slot(self):
        token, _ = self.create_conflict_and_hold()
        with app.app_context():
            hold_id = EventReservation.query.filter_by(status="Held").one().id
        declined = self.post(f"/events/recommendations/{token}/decline")
        self.assertEqual(declined.status_code, 302)
        with app.app_context():
            hold = db.session.get(EventReservation, hold_id)
            self.assertEqual(hold.status, "Released")
            self.assertIsNotNone(hold.released_at)
            self.assertIsNone(hold.hold_token_hash)
            self.assertIsNone(hold.draft_payload)

    def test_expired_event_hold_is_persistently_released(self):
        token, _ = self.create_conflict_and_hold()
        with app.app_context():
            hold = EventReservation.query.filter_by(status="Held").one()
            hold_id = hold.id
            hold.hold_expires_at = datetime.utcnow() - timedelta(seconds=1)
            before_events = EventRequest.query.count()
            db.session.commit()
        expired = self.post(f"/events/recommendations/{token}/accept")
        self.assertEqual(expired.status_code, 302)
        with app.app_context():
            hold = db.session.get(EventReservation, hold_id)
            self.assertEqual(hold.status, "Expired")
            self.assertIsNotNone(hold.released_at)
            self.assertEqual(EventRequest.query.count(), before_events)

    def test_adjacent_event_intervals_do_not_overlap(self):
        self.auth("resident")
        first = self.post(
            "/events", self.future_event_data(days=50, start="09:00", end="10:00")
        )
        self.assertEqual(first.status_code, 302)
        self.auth("other")
        adjacent = self.post(
            "/events",
            self.future_event_data(
                days=50, start="10:00", end="11:00", name="Adjacent Event"
            ),
        )
        self.assertEqual(adjacent.status_code, 302)
        with app.app_context():
            self.assertEqual(EventRequest.query.count(), 2)
            self.assertEqual(
                EventReservation.query.filter_by(status="Reserved").count(), 2
            )

    def test_acknowledgments_permit_document_and_reports_are_editable_docx_only(self):
        with app.app_context():
            permit = PermitApplication(
                applicant_id=self.ids["resident"],
                permit_type_id=self.ids["permit_type"],
                purpose="Editable document test",
                fee_at_submission=Decimal("50.00"),
                status="Ready for Pickup",
                decision_date=datetime.utcnow(),
                signed_by=self.ids["admin"],
                signed_at=datetime.utcnow(),
                signature_path="test-signature.png",
                signatory_name="Authorized Test Official",
                signatory_title="Barangay Captain",
            )
            event = EventRequest(
                requester_id=self.ids["resident"],
                event_name="Document Test Event",
                category_id=self.ids["event_category"],
                venue_id=self.ids["venue"],
                event_type="Community Activity",
                description="Document generation",
                proposed_date=date.today() + timedelta(days=60),
                proposed_start_time=time(9, 0),
                proposed_end_time=time(11, 0),
                proposed_location="Minante Covered Court",
                expected_attendees=0,
                fee_at_submission=Decimal("250.00"),
            )
            blotter = BlotterCase(
                complainant_id=self.ids["resident"],
                respondent_name="Respondent Person",
                incident_type="Incident",
                incident_date=datetime.utcnow() - timedelta(days=1),
                incident_location="Purok 1",
                narrative="Confidential incident detail",
            )
            db.session.add_all([permit, event, blotter])
            db.session.commit()
            ids = {"permit": permit.id, "event": event.id, "blotter": blotter.id}

        self.auth("resident")
        for kind, record_id in ids.items():
            response = self.client.get(f"/acknowledgments/{kind}/{record_id}.docx")
            self.assertEqual(response.status_code, 200, kind)
            self.assertEqual(response.mimetype, DOCX_MIMETYPE)
            self.assertEqual(response.data[:2], b"PK")
            self.assertIn(".docx", response.headers["Content-Disposition"])
            self.assertIn("SUBMISSION ACKNOWLEDGMENT", self.docx_xml(response))

        approved = self.client.get(
            f"/permits/{ids['permit']}/approved-document.docx"
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.mimetype, DOCX_MIMETYPE)
        self.assertEqual(approved.data[:2], b"PK")
        self.assertIn(".docx", approved.headers["Content-Disposition"])
        self.assertEqual(
            self.client.get(f"/receipts/permit/{ids['permit']}.pdf").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/permits/{ids['permit']}/approved-document").status_code,
            404,
        )
        self.auth("other")
        self.assertEqual(
            self.client.get(f"/acknowledgments/permit/{ids['permit']}.docx").status_code,
            403,
        )

    def test_schedule_creation_redirect_validation_and_record_ownership(self):
        owned_id, owned_reference = self.create_permit(owner="resident")
        hidden_id, hidden_reference = self.create_permit(owner="other")
        self.auth("resident")
        page = self.client.get("/schedules")
        self.assertEqual(page.status_code, 200)
        self.assertIn(owned_reference.encode(), page.data)
        self.assertNotIn(hidden_reference.encode(), page.data)
        self.assertIn(b'name="related_record"', page.data)

        scheduled = (datetime.now() + timedelta(days=7)).replace(
            second=0, microsecond=0
        )
        created = self.post(
            "/schedules",
            {
                "related_record": f"permit:{owned_id}",
                "scheduled_datetime": scheduled.isoformat(timespec="minutes"),
                "purpose": "Document follow-up",
            },
        )
        self.assertEqual(created.status_code, 302)
        with app.app_context():
            item = Schedule.query.one()
            self.assertEqual(item.related_type, "permit")
            self.assertEqual(item.related_id, owned_id)
            self.assertEqual(item.requested_by, self.ids["resident"])

        for invalid in ("", "invalid", "permit:0", "permit:999999"):
            response = self.post(
                "/schedules",
                {
                    "related_record": invalid,
                    "scheduled_datetime": scheduled.isoformat(timespec="minutes"),
                    "purpose": "Invalid selection",
                },
            )
            self.assertEqual(response.status_code, 302, invalid)
        self.assertEqual(
            self.post(
                "/schedules",
                {
                    "related_record": f"permit:{owned_id}",
                    "scheduled_datetime": "not-a-date",
                    "purpose": "Invalid date",
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            self.post(
                "/schedules",
                {
                    "related_record": f"permit:{hidden_id}",
                    "scheduled_datetime": scheduled.isoformat(timespec="minutes"),
                    "purpose": "Unauthorized",
                },
            ).status_code,
            403,
        )
        with app.app_context():
            self.assertEqual(Schedule.query.count(), 1)

    def test_report_uses_real_counts_and_exports_only_docx(self):
        self.create_permit(owner="resident", status="Pending")
        self.create_permit(owner="other", status="Approved")
        with app.app_context():
            db.session.add(
                Notification(
                    user_id=self.ids["resident"],
                    title="Real notification",
                    message="Persisted notification",
                    channel="in_app",
                )
            )
            db.session.commit()
        self.auth("admin")
        page = self.client.get("/reports")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Pending", page.data)
        self.assertIn(b"Approved", page.data)

        report = self.post("/reports/export/docx")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.mimetype, DOCX_MIMETYPE)
        self.assertEqual(report.data[:2], b"PK")
        report_xml = self.docx_xml(report)
        self.assertIn("BARANGAY SYSTEM MONITORING REPORT", report_xml)
        self.assertIn("Pending", report_xml)
        self.assertIn("Approved", report_xml)
        with app.app_context():
            saved = Report.query.one()
            self.assertEqual(saved.generated_by, self.ids["admin"])
            self.assertEqual(saved.format, "docx")
        self.assertEqual(self.post("/reports/export/pdf").status_code, 404)
        self.assertEqual(self.post("/reports/export/csv").status_code, 404)

    def test_role_boundaries_primary_pages_and_ten_second_toasts(self):
        self.auth("staff")
        for url in (
            "/profile",
            "/announcements",
            "/permits",
            "/events",
            "/blotters",
            "/schedules",
            "/notifications",
            "/users",
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for url in ("/reports", "/activity-logs", "/configuration"):
            self.assertEqual(self.client.get(url).status_code, 403, url)

        self.auth("resident")
        self.assertEqual(self.client.get("/users").status_code, 403)
        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"10000", dashboard.data)

        self.auth("admin")
        for url in (
            "/dashboard",
            "/permits",
            "/events",
            "/blotters",
            "/schedules",
            "/notifications",
            "/users",
            "/reports",
            "/activity-logs",
            "/configuration",
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_sqlite_integrity_and_migration_are_idempotent(self):
        with app.app_context():
            admin = db.session.get(User, self.ids["admin"])
            original_hash = admin.password_hash
            original_user_count = User.query.count()
            upgrade_legacy_schema()
            upgrade_legacy_schema()
            self.assertEqual(User.query.count(), original_user_count)
            self.assertEqual(
                db.session.get(User, self.ids["admin"]).password_hash, original_hash
            )
            self.assertEqual(db.engine.dialect.name, "sqlite")
            self.assertEqual(db.session.execute(text("PRAGMA foreign_keys")).scalar(), 1)
            self.assertEqual(
                db.session.execute(text("PRAGMA integrity_check")).scalar().lower(),
                "ok",
            )
            invalid = PermitApplication(
                applicant_id=999999,
                permit_type_id=999999,
                purpose="Must fail",
                fee_at_submission=Decimal("0.00"),
            )
            db.session.add(invalid)
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
            self.assertIsNotNone(db.session.get(User, self.ids["admin"]))
        health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["database"], "sqlite")

    def test_password_reset_token_is_single_use_and_preserves_role(self):
        with app.app_context():
            user = db.session.get(User, self.ids["admin"])
            raw_token, _ = PasswordResetToken.issue(user)
            db.session.commit()
        self.clear_auth()
        reset = self.post(
            f"/reset-password/{raw_token}",
            {"password": "NewSecure456", "confirm_password": "NewSecure456"},
        )
        self.assertEqual(reset.status_code, 302)
        self.assertEqual(self.client.get(f"/reset-password/{raw_token}").status_code, 302)
        with app.app_context():
            user = db.session.get(User, self.ids["admin"])
            self.assertEqual(user.role, "admin")
            self.assertTrue(user.check_password("NewSecure456"))
            self.assertTrue(
                ActivityLog.query.filter_by(
                    action="PASSWORD_RESET_COMPLETED", user_id=user.id
                ).count()
            )


if __name__ == "__main__":
    unittest.main()
