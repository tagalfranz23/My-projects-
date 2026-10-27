# Barangay Management System Revision Notes

This note summarizes the current application revisions prepared for the Render deployment. It was checked against the Flask routes, services, templates, SQLite migration code, tests, and the repository's Render configuration.

## Major system changes

- **Resident Services scope:** Removed the Scholarship/Ayuda interface and exclusive flows, General Concern Intake, and the Resident Update Request, Household Management, and Assistance Referral panels/endpoints. Existing historical records and shared data structures are retained. Blotter Management remains separate and available.
- **Walk-in Front Desk:** Added assisted resident intake for staff, service-specific Permit, Event, Blotter, and Appointment requests, duplicate-resident matching, queue numbers and states, encoder tracking, printable acknowledgments, and manual follow-up.
- **Resident information:** Added family-head and repeatable family-member fields, plus resident-only disability information. Authorized staff can review and update saved details; children do not receive accounts. Reports use aggregate data and do not expose individual disability details.
- **Permit workflow:** Preserved administrator authorization, document verification, remarks, notifications, acknowledgments, and approved-permit downloads. Payment status and electronic signatures no longer gate permit status transitions. Permit Details omits the Payment Status section and View Payments link; retained payment records and other payment interfaces are not changed by permit updates.
- **Event workflow:** Added the availability calendar to the existing event application flow, using date/time/venue conflicts and current permit status. Pending applications remain distinct from approved bookings. Event status updates retain authorization and notification handling; payment is not required to mark an approved event Completed.
- **Scheduling:** Fixed status form value handling and validation so supported status selections are saved and invalid values are rejected with a useful response.
- **Reports & Analytics:** Removed report filter controls and load SQLite-backed Permit, Event, Blotter, and Overall Barangay summaries automatically. The Overall report includes population, gender, disability yes/no/unknown, and disability-type aggregates. The report download is PDF; the editable Word report endpoint is removed.
- **Security and data handling:** Retained role and ownership checks, CSRF validation, audit logging, private identification uploads, and additive SQLite migrations. Sensitive resident and household records remain in the database and are not included in this repository update.

## Deployment and validation

- Render is configured to deploy commits on `main`, with `barangay_system` as its application root.
- The complete backend unit suite passed locally: `python -B -m unittest discover -s tests -p "test_*.py"` — **97 tests passed**.
- Permit browser checks also passed for the status workflow and signature-free permit processing, including acknowledgment and approved-permit downloads.
- The Render manifest currently points SQLite data to `/tmp/minante-data`. Render's ephemeral filesystem can lose records and private uploads on restart or redeploy; configure persistent storage before relying on the hosted system for real records.

## Files intentionally kept out of the deployment commit

Local databases, resident uploads, environment secrets, virtual environments, backups, audit output, screenshots, generated ZIP archives, and generated PDF/Word documentation are excluded. The commit contains application code, templates, static assets, setup requirements, and automated tests only.
