# Barangay Minante 1 Integrated Management System

**Development of an Integrated Barangay Permit, Event Approval, Blotter Management System with Notification and Scheduling Features for Barangay Minante 1, Cauayan City**

Location: **Barangay Minante 1, Cauayan City, Isabela**

This major revision centralizes resident accounts, permits, independent event approvals, blotter cases, first-class schedules, SMS/email/in-app notifications, administrator reporting, and immutable audit history. It replaces the legacy top navigation with a role-aware collapsible desktop sidebar and accessible mobile drawer.

The current visual system follows the supplied Barangay Minante 1 reference: deep navy, civic gold, clean white surfaces, modern sans-serif typography, a responsive authentication split layout, role-specific dashboards, standardized cards/forms/tables/statuses, and an off-canvas mobile navigation drawer.

## Submission acknowledgment receipts

After a valid Resident Permit, Event, or Blotter submission commits to SQLite, the system redirects to a Submission Successful page. An authorized user can generate a one-page PDF acknowledgment on demand from the exact saved row. The receipt uses the transaction reference number, resident name, real submission timestamp, current status, relevant summary, configurable processing estimate, next steps, appropriate requirements, and a prominent non-approval disclaimer.

Receipt generation is separate from approved Permit documents. A generation failure never rolls back or deletes the committed submission, and Resident ownership is enforced server-side. Staff and Administrators may download only records they are authorized to view. Administrators configure processing estimates under System Configuration using `receipt_processing_permit`, `receipt_processing_event`, and `receipt_processing_blotter` settings.

## Architecture

The seven Level-1 processes and their corresponding implementation are:

1. **1.0 User Management** — registration and administrator account management.
2. **2.0 Permit Processing** — resident submission, Staff review/endorsement, Admin decision/completion.
3. **3.0 Event Approval Management** — independent Event Requests with the same separated decision authority.
4. **4.0 Blotter Management** — filing, intake, investigation, hearing, resolution, and closure.
5. **5.0 Scheduling and Notification Management** — related schedules and distinct SMS, email, and in-app records.
6. **6.0 Reports and Monitoring** — administrator metrics plus CSV/PDF export and report history.
7. **7.0 Activity Logging** — centralized audit events for state changes and security actions.

The eight required stores are represented by `users`, `permit_applications`, `event_requests`, `blotter_cases`, `schedules`, `notifications`, `activity_logs`, and `reports`. Supporting catalogs and hashed, expiring, single-use password-reset tokens have separate tables.

## Setup

```powershell
cd barangay_system
..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATA_DIR = "instance"
$env:SQLITE_DB_PATH = "instance/minante.db"
$env:FLASK_APP = "app.py"
python seed.py
flask run
```

SQLite is now the required operational database. Locally, the default file is `instance/minante.db`. `flask init-db` performs an additive, idempotent schema initialization; it does not drop users or replace the permanent Administrator. `python seed.py` preserves an existing Admin and adds only legitimate configuration catalogs—never Staff or Resident sample accounts.

For a completely fresh installation with no Admin, set `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_NAME`, and a strong `INITIAL_ADMIN_PASSWORD`, then run `flask create-admin` or `python seed.py`. Credentials are deliberately not stored in source code or documentation. Residents self-register; an authenticated Administrator creates Staff or additional Admin accounts.

### One-time MariaDB to SQLite migration

The included migration utility reads MariaDB without modifying it and writes only to an empty SQLite target:

```powershell
pip install -r requirements-migration.txt
$env:MARIADB_SOURCE_HOST = "127.0.0.1"
$env:MARIADB_SOURCE_PORT = "3306"
$env:MARIADB_SOURCE_USER = "your-private-user"
$env:MARIADB_SOURCE_PASSWORD = "your-private-password"
$env:MARIADB_SOURCE_DB = "your-current-database"
$env:SQLITE_DB_PATH = "instance/minante.db"
python migrate_mariadb_to_sqlite.py
```

The utility refuses a populated target, copies all available operational tables in one transaction, preserves IDs/password hashes/references/history, verifies row counts and foreign keys, and requires an active permanent Administrator before commit. The former MariaDB schema SQL files remain only as restricted historical/migration references; the running application does not use them.

### Render deployment without a separate database

The repository-level `render.yaml` creates one Python 3.13 web service in Render's Singapore region with a 1 GB persistent disk mounted at `/var/data`. It runs schema/catalog initialization before Gunicorn, starts exactly one worker with four threads, and exposes `/healthz` for database-aware health checks.

1. Push the repository to GitHub or GitLab.
2. In Render, create a new Blueprint from the repository so `render.yaml` is applied.
3. Select a paid web-service plan. Render Free web services cannot attach a persistent disk and will erase local SQLite data when they restart or redeploy.
4. Supply the four `INITIAL_ADMIN_*` secret values when the disk is fresh. After the first successful initialization, `seed.py` preserves that account and does not recreate it.
5. Keep the disk mounted at `/var/data`, keep exactly one service instance, and do not increase Gunicorn above one worker.
6. If preserving migrated MariaDB records, upload the validated `minante.db` to `/var/data/minante.db` before opening the service to users; do not run the migration against a live populated SQLite target.

Only `/var/data` persists. The database and uploaded supporting documents are therefore both configured beneath that path. An attached disk prevents horizontal scaling and disables zero-downtime deploys; brief deployment downtime is expected. Render disk snapshots provide a recovery layer, and `python backup_sqlite.py` creates an application-consistent backup that can be downloaded before major revisions.

Production must set a strong `SECRET_KEY`, `DATA_DIR=/var/data`, `SQLITE_DB_PATH=/var/data/minante.db`, `SESSION_COOKIE_SECURE=1`, and `FLASK_DEBUG=0`. A `DATABASE_URL` override is accepted only when it begins with `sqlite:`. Secrets must never be committed, logged, or rendered in the frontend. Simulated email/SMS deliveries are stored separately in Notifications; replace the service adapter with a real provider without changing workflows.

## Permissions

| Resource | Resident | Barangay Staff | Administrator |
|---|---|---|---|
| Own profile | Allowed fields | Own profile | Full authorized management |
| Users | No | Read-only list | Full management |
| Permits | Own submission/history | Review and endorse | Final approval/rejection and full workflow |
| Event Requests | Own submission/history | Review and endorse | Final approval/rejection and full workflow |
| Blotter | Own records | Intake/review/authorized updates | Full case decisions |
| Scheduling | Own/request | Operational schedules | Full scheduling control |
| Notifications | Own history/read state | Operational | Full |
| Staff work summary | No | Yes | Yes |
| Reports/export | No | No | Yes |
| Activity logs | No | No | Yes |
| Configuration | No | No | Yes |

Permissions are enforced by centralized backend rules in `permissions.py`; sidebar visibility is only a presentation of those rules. Status changes are checked against role-specific transitions in `workflows.py`, and resident ownership is checked server-side.

## Password recovery and environment safety

The Login page links to Forgot Password. Recovery uses the registered email, returns an account-enumeration-safe response, issues a cryptographically random token stored only as a SHA-256 hash, expires it after 30 minutes, invalidates earlier/used tokens, hashes the new password, and records `PASSWORD_RESET_COMPLETED` without logging credentials or tokens.

Development email delivery can show the reset link only when Flask debug mode is explicitly enabled. Production never displays it. Automated tests use an isolated temporary database and never modify the permanent administrator.

## Validation

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The suite verifies representative page rendering, Admin/Staff/Resident access boundaries, ownership isolation, forbidden Staff final approval, CSRF-aware workflows, and secure recovery-token storage. Production data initialization and test/sample data are intentionally separate.

Run `python tests/validate_sqlite.py` against a development copy of SQLite to verify durability pragmas, real login, every required data store, a separate-process restart read, foreign-key enforcement, rollback, cleanup, and permanent Administrator preservation. Never point validation or browser-QA scripts at the production database.

Current Render platform details and limitations should be checked against the official [persistent disk documentation](https://render.com/docs/disks), [free tier documentation](https://render.com/docs/free), and [Flask deployment guide](https://render.com/docs/deploy-flask) before provisioning.

See the repository-level [`RENDER_DEPLOYMENT.md`](../RENDER_DEPLOYMENT.md) for the complete fresh-deploy, migrated-data transfer, update, backup, and production acceptance procedure.
