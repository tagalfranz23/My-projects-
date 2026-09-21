# Barangay Minante 1 Integrated Management System

This Flask system uses a persistent SQLite database for Barangay Minante 1, Cauayan City, Isabela. It manages resident accounts, permits, events, blotter cases, schedules, notifications, logs, reports, and approved system configuration.

## Resident verification workflow

Resident Type and Residency Status are separate current-profile fields. New registrations require an Administrator-accepted valid ID document, start with pending ID and address verification, and cannot be approved until both are Verified by an Administrator. Uploaded ID documents are stored privately, are not part of reports or notifications, and can only be opened by the owning resident or an Administrator.

The Administrator configures supported ID types and decides which are accepted for registration. It can also manage Resident Types, fees, venues, and other approved operational settings. Existing users and historical transactions are preserved by additive SQLite migrations.

## Permit, walk-in, and release workflow

Each permit service can have Administrator-managed document, text, or system-check requirements, a configured fee, and an estimated processing time. The exact requirements and fee are snapshotted when a resident submits, so later configuration changes never alter a historical request. Staff verify individual requirements and must give a reason when requesting resubmission. A request cannot be endorsed until every required item is verified; a staff member can never process their own request.

After Administrator approval, the designated Administrator applies an authorized electronic signature that is stored privately with an audit entry. If a fee applies, Staff or an Administrator records the in-person payment confirmation before the permit becomes ready for pickup. The release counter records an identity check and the releasing staff member. The application does not claim that an electronic signature is a cryptographic certificate or a substitute for a physical process required by policy.

Staff and Administrators can use **Resident Services** for walk-in assistance: search the existing resident by name, contact number, household/purok, or numeric Resident ID; select an active permit service; and create the normal permit workflow with source, encoder, office, queue number, and editable Word claim stub recorded. The same area includes queue actions, general concerns, profile-update requests, household management, referrals, and feedback collection. Staff announcements are saved as drafts and require Administrator approval before publication.

## Editable downloads

Submission acknowledgments, approved permits, and monitoring reports are generated only as editable Microsoft Word `.docx` documents.

## Local setup

```powershell
cd barangay_system
..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SQLITE_DB_PATH = "instance/minante.db"
$env:INITIAL_ADMIN_USERNAME = "private-admin"
$env:INITIAL_ADMIN_EMAIL = "admin@example.invalid"
$env:INITIAL_ADMIN_NAME = "System Administrator"
$env:INITIAL_ADMIN_PASSWORD = "A-private-strong-password-123"
python seed.py
flask --app app.py run
```

## Render note

Render Free has an ephemeral filesystem. A SQLite database and private uploads will not survive a restart or redeploy there. Use it for demonstrations only; production SQLite requires a persistent mounted disk and one service instance. See [`RENDER_DEPLOYMENT.md`](../RENDER_DEPLOYMENT.md).

## Validation

```powershell
cd barangay_system
..\.venv\Scripts\python.exe -m py_compile config.py models.py migration.py app.py seed.py
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
