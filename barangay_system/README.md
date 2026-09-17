# Barangay Minante 1 Integrated Management System

This Flask system uses a persistent SQLite database for Barangay Minante 1, Cauayan City, Isabela. It manages resident accounts, permits, events, blotter cases, schedules, notifications, logs, reports, and approved system configuration.

## Resident verification workflow

Resident Type and Residency Status are separate current-profile fields. New registrations require an Administrator-accepted valid ID document, start with pending ID and address verification, and cannot be approved until both are Verified by an Administrator. Uploaded ID documents are stored privately, are not part of reports or notifications, and can only be opened by the owning resident or an Administrator.

The Administrator configures supported ID types and decides which are accepted for registration. It can also manage Resident Types, fees, venues, and other approved operational settings. Existing users and historical transactions are preserved by additive SQLite migrations.

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
