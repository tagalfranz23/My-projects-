# Barangay Minante 1 Integrated Management System

This Flask system uses a persistent SQLite database for Barangay Minante 1, Cauayan City, Isabela. It manages resident accounts, permits, events, blotter cases, schedules, notifications, logs, reports, and approved system configuration.

## Resident verification workflow

Resident Type and Residency Status are separate current-profile fields. New registrations require an Administrator-accepted valid ID document, start with pending ID and address verification, and cannot be approved until both are Verified by an Administrator. Uploaded ID documents are stored privately, are not part of reports or notifications, and can only be opened by the owning resident or an Administrator.

The Administrator configures supported ID types and decides which are accepted for registration. It can also manage Resident Types, fees, venues, and other approved operational settings. Existing users and historical transactions are preserved by additive SQLite migrations.

## Permit, walk-in, and release workflow

Each permit service can have Administrator-managed document, text, or system-check requirements, a configured fee, and an estimated processing time. The exact requirements and fee are snapshotted when a resident submits, so later configuration changes never alter a historical request. Staff verify individual requirements and must give a reason when requesting resubmission. A request cannot be endorsed until every required item is verified; a staff member can never process their own request.

After Administrator approval, authorized administrators can continue the existing permit status workflow, including Ready for Pickup and Completed when allowed by the workflow. Permit status changes do not require payment recording or an electronic signature; required document verification and administrator authorization still apply. Permit Details no longer displays Payment Status or a View Payments link. Existing payment records and separate payment interfaces are preserved, and permit status updates do not change payment data. The release counter records an identity check and the releasing staff member.

Staff and Administrators can use **Resident Services** for assisted Permit, Event, Blotter, and Appointment requests. Search an existing resident first, or choose **New Walk-in Resident**. Enter the resident's name, address/purok and privacy consent; birth date, mobile number and scanned ID are optional at intake. Possible matches by name/address, name/birth date or phone return staff to search rather than create a duplicate. Matches require staff confirmation and are never automatically merged.

Offline residents reuse `users` and `resident_profiles` for ownership, with a random internal identifier and unavailable password; `online_access_enabled=False` blocks login, authenticated sessions and password recovery. Residents do not need credentials, email or gadgets. Identity documents can be added later by staff using the selected resident's scanned-ID form. Administrator identity/address verification and registration approval remain required. Services configured with required system checks cannot be submitted before the resident satisfies those checks. Permit requirements, fees, endorsements, signing and release still use the existing workflow. Event intake reuses venue conflict checks; an unavailable slot is rejected without saving a request or queue ticket, and staff must choose another time. Appointment requests must link to the selected resident's existing Permit, Event or Blotter record.

Each assisted request receives a normal service reference and daily queue ticket. The queue records the encoding staff member and service link; audit logs record intake and updates. Daily ticket allocation takes a SQLite writer lock to avoid duplicate numbers. Queue states are Waiting, Now Serving/Serving (shown as Being assisted), and Completed. Completing assistance never approves or completes the underlying request. Permit acknowledgments retain printing and editable Word downloads; the other services have printable slips without blotter narratives, respondent names or locations. **View References / Manual Follow-up** lists the selected resident's current request statuses and reprints slips, including prior-day requests. Resident Update Request, Household Management and Assistance Referral intake/review/history panels and their exclusive endpoints have been removed to match the research scope. Their historical tables remain intact. Household fields in resident registration, profile editing and search remain part of User Management. General Concern Intake and Recent Concerns (which used separate general-concern records, not blotters) have been removed, together with their intake/routing endpoints and exclusive summary/report logic. Historical general-concern records and the model/table are preserved; Blotter Management is unchanged.

In-app notifications remain stored. Email records are created only if an email is present; SMS records only if a valid phone is present. The existing SMS/email delivery remains simulated and does not contact a provider. Residents without a phone should bring their printed reference to the desk. Existing permissions protect the desk, reference lookup, ID intake and slips; no public resident lookup is added.

Restart using `run.ps1` after updating. Startup applies only additive migrations: an online-access flag (enabled for existing users), and nullable `related_type`/`related_id` queue columns. No resident or transaction records are removed. For a WSGI deployment, run the existing `flask --app app init-db` initialization command before restarting workers.

Validation from `barangay_system`: `..\.venv\Scripts\python.exe -m unittest discover -s tests -v`. The optional `..\.venv\Scripts\python.exe tests\browser_walkin_assistance.py` verifies all four forms, slips, queue actions and responsive layouts using an isolated synthetic SQLite database and installed Microsoft Edge.

## Preserved payment records and announcements

Payment tables, existing records, and separate payment interfaces remain preserved. Payment status, payment recording, waivers, and electronic signatures are not prerequisites for Permit Processing, and permit status updates never create or modify payment records. No payment gateway, partial payments, refunds, or additional charges are provided. Historical payment decisions cannot be edited through the application.

Scholarship and Ayuda program pages, application handlers, and reports have been removed. Historical program tables and models remain to preserve records and announcement relationships. Announcement categories remain available; announcements no longer link to program pages.

Announcements accept publication and expiration date/time in **Asia/Manila**, store UTC timestamps, and publish/expire on backend access without a scheduler. Active-feed queries always enforce time boundaries. Expired announcements remain in history; Archive preserves the record. Old announcements without an expiration keep their existing behavior. Newly created announcements require both timestamps. Publication/expiration logs are recorded on the next dashboard, announcements, or reports access.

## Analytics and PDF reports

The Administrator dashboard contains a concise summary. **Reports & Analytics** automatically displays all-time Permit, Event Request, Blotter and Overall summaries from SQLite without filter controls. Charts and exact data tables exclude private applicant documents and case narratives.

Report downloads use ReportLab PDF output with charts rendered by matplotlib. Report views and PDF exports are audited. PDF editing/copying restrictions apply in compatible viewers; they do not provide tamper-proof certification. Online submissions retain Submission Acknowledgment terminology; paid transactions use Payment Receipt.

## Additive SQLite upgrade

Back up the configured database and private upload directories before deploying to a live installation. Start through `run.ps1` / `app.py`, or run `python -m flask --app app init-db` before starting a WSGI server. Migrations add missing tables, columns, and query indexes without deleting records. They do not invent payments for old payment-confirmation flags. Review any historical paid/released records lacking payment evidence rather than silently backfilling them. Existing fees remain unchanged.

SMS and email are currently **simulated**, not delivered by a live provider. Password-reset token handling is tested, but real recovery-message delivery requires provider integration.

## Editable downloads

Submission acknowledgments and approved permits retain their existing Microsoft Word `.docx` downloads. Monitoring reports now export only as PDF.

## Local setup

From the repository root, create the local environment and install dependencies:

```powershell
.\setup.ps1
.\run.ps1
```

The setup script can be rerun after downloading the project or changing its
requirements. The run script always uses this project's `.venv` interpreter.
VS Code settings are included for opening either the repository or the
`barangay_system` folder. If VS Code previously selected another interpreter,
run **Python: Select Interpreter**, select the repository's
`.venv\Scripts\python.exe`, then run **Developer: Reload Window** once.

For first-time administrator creation, set your own credentials and run:

```powershell
cd barangay_system
$env:SQLITE_DB_PATH = "instance/minante.db"
$env:INITIAL_ADMIN_USERNAME = "private-admin"
$env:INITIAL_ADMIN_EMAIL = "admin@example.invalid"
$env:INITIAL_ADMIN_NAME = "System Administrator"
$env:INITIAL_ADMIN_PASSWORD = "A-private-strong-password-123"
..\.venv\Scripts\python.exe seed.py
..\.venv\Scripts\python.exe app.py
```

## Render note

Render Free has an ephemeral filesystem. A SQLite database and private uploads will not survive a restart or redeploy there. Use it for demonstrations only; production SQLite requires a persistent mounted disk and one service instance. See [`RENDER_DEPLOYMENT.md`](../RENDER_DEPLOYMENT.md).

## Validation

```powershell
cd barangay_system
..\.venv\Scripts\python.exe -m py_compile config.py models.py migration.py app.py seed.py
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Protected report downloads

Reports & Analytics exports PDF through **Download PDF Report**. The former report DOCX endpoint now returns 404. PDFs include live core service aggregates, charts, tables, generation details and page numbers. Printing is allowed; editing, copying and annotations are restricted in compliant PDF viewers using a random owner password. These permissions are not a digital signature and cannot guarantee detection or prevention of tampering. Keep the generated original for comparison. Permit documents and acknowledgment Word downloads are unchanged. Install application requirements after updating; PDF support requires ReportLab. Test dependencies are in `tests/requirements.txt`.

## Staff resident family and disability information

The staff-facing **New Walk-in Resident** form is the resident-information creation screen. It now asks whether the resident is the head of the family and whether the resident has a disability. Choosing Yes for head of family shows repeatable family-member rows (full name, integer age 0-120, Male/Female gender). Only unsaved rows have Remove buttons. No family member receives a user or login account. Disability questions appear only once for the resident, with the configured choices including Other and Prefer not to specify. Other has an optional description; choosing No clears the stored type and description.

After selecting a resident in Resident Services, use **View / Update Resident Information**. Administrators can also open **Family and Disability Information** from the existing user-review page. These details and edits require existing front-desk management permission (Staff/Administrator); residents have no access. Pages are marked private/no-store. Audit records identify the operator and resident record without including children's names or disability details. These fields are not included in acknowledgments, notifications or reports.

`resident_profiles` gains nullable `has_disability`, `disability_type` and `disability_other` columns. Existing residents remain unclassified until personnel record an answer. The existing `is_household_head` field supplies the family-head answer. New `resident_family_members` rows link to the correct resident profile and, where an unambiguous existing household is identified, its household ID. They store member name, age, gender, a normalized duplicate-prevention key, and creating/updating operators. Existing household rows, memberships and links are never transferred, recreated or deleted by this form.

Saved members are updated by their verified record IDs. Omitted rows, selecting No for head of family, and repeat submissions do not delete saved members or create duplicates. Details always display saved family members; changing the head answer to Yes shows them for editing again. Registration (`/register`), its template, Permit Application and its template are unchanged. Existing account/ID approval and service workflows remain in force.

Restart through `run.ps1` to apply the existing additive migration, or run `python -m flask --app app init-db` before restarting a WSGI deployment. No data-deleting migration is required. Tests: `python -m unittest discover -s tests -p test_resident_family.py -v`; browser check: `python tests/browser_resident_family.py` using installed Microsoft Edge and isolated synthetic records.

## Event Availability Calendar

The existing **Event Approval Requests** page now includes a current-month calendar with previous/next navigation, a venue selector, optional exact start/end times, and a legend. Pick a venue and both times to check a specific interval. Clicking an available date fills the existing Event Request form; clicking a booked or unavailable date shows permitted information without selecting it for submission. Partially reserved dates remain selectable when free time exists. Past slots cannot be selected through the calendar. Availability refreshes every 30 seconds, on window focus, and with Refresh.

The existing reservation policy remains: Pending, Under Review, Endorsed to Admin and Approved requests reserve their slots; a pending reservation is labelled separately from an approved booking. Live temporary recommendation holds also block overlap; expired holds do not. Rejected, Cancelled and Completed requests do not block. Adjacent intervals are allowed and availability is checked separately for each active venue. Legacy requests without reservation rows participate using their existing venue/date/time; missing legacy times are conservatively reserved until an Administrator supplies a schedule. No new events, tables or migration are generated by viewing the calendar.

Calendar access requires a signed-in approved account. Residents see only reservation intervals and generic status labels for other applicants. Their own events and events viewed by Staff/Administrators include a title/reference and link to the existing authorized details page. Applicant identity/contact data, descriptions, review remarks and private hold tokens are not returned by the calendar endpoint. Responses are private/no-store.

Administrators can change an active event's venue/time from its details page using **Change event venue / schedule**. The existing reservation is moved under the SQLite venue lock and checked for conflicts before saving; the event's approval state and fee snapshot remain unchanged. Administrators can also Cancel an Approved event through its existing status form. Rejection/cancellation release the reservation, and approval performs another overlap check. Residents and Staff cannot use the Administrator reschedule endpoint, and Staff still cannot process their own requests. Event submissions remain Pending; the calendar never grants approval. The existing conflict recommendation/temporary hold flow is preserved.

Changed event tables: `event_requests` and `event_reservations` are reused for normal submissions, reschedules and cancellation; no columns or tables are added. Other modules and resident registration are unchanged. Tests: `python -m unittest discover -s tests -p test_event_calendar.py -v`; headless Edge check: `python tests/browser_event_calendar.py` with isolated synthetic fixtures.

## Overall population, gender and disability reports

The existing Overall Barangay Report includes Population Statistics, Gender Distribution, Disability Statistics and Disability Distribution by Type. Web tables/charts and the protected PDF use the same `population_sections()` result. Existing service reports remain alongside them; no report filters, new page or schema changes are introduced.

Population eligibility follows the existing active-approved resident rule: `users.role` is Resident or Staff, `users.is_active` is true and the uniquely linked `resident_profiles.approval_status` is Approved. Each distinct `users.id` counts once. Approved offline residents are included; Pending/Rejected/inactive accounts, administrator-only accounts and profiles without a resident user are excluded. Family-only records never count as registered residents. No grouping by names is performed, since different residents may have identical names. Household/family tables are not joined, avoiding multiplied totals.

Gender uses trimmed, case-normalized `resident_profiles.sex`. Male/Female counts share the same eligible population; percentages divide each by Male + Female. Missing/unrecognized values are reported separately and do not enter that denominator; an empty denominator yields 0%. Disability Yes/No uses explicit true/false in `has_disability`; NULL remains unknown and is listed separately, never treated as No. Type counts use only Yes records and the existing form's disability category choices. Missing/unrecognized types are grouped as Not recorded / unspecified. Free-text Other descriptions, resident names and children's details are never exported by these queries. Existing Reports access remains Administrator-only; other roles are not granted extra reporting access.

Tests: `python -m unittest discover -s tests -p test_population_reports.py -v`. Fixtures include Male/Female/unknown gender, Yes/No/unknown disability, missing types, approved offline records, excluded accounts and family/membership records. Tests compare the page and PDF values against known SQLite rows, check aggregate privacy and authorization, and cover an empty eligible population. Existing production records are not modified by the tests.
