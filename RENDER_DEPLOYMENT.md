# Render Deployment — SQLite With No Separate Database

This application is configured as one Render Python web service with one attached Persistent Disk. SQLite and uploaded evidence are stored beneath `/var/data`, so no separately deployed database service is required.

## Non-negotiable safety rules

- Use the paid `0.5c-512mb` web-service plan (or larger). Render Free web services cannot attach a Persistent Disk and erase local SQLite files on restart, spin-down, or redeploy.
- Keep `numInstances: 1` and Gunicorn `--workers 1`. SQLite supports many readers but only one writer at a time, and an attached Render disk cannot be shared by multiple instances.
- Keep `DATA_DIR=/var/data` and the active SQLite file under `/var/data`.
- Never commit `minante.db`, its `-wal`/`-shm` files, backups, or resident uploads to Git.
- Create/download a verified backup before schema changes or a production release.
- Expect brief downtime during deployment because a service with an attached disk does not use zero-downtime instance replacement.

Official references: [Render Persistent Disks](https://render.com/docs/disks), [Render Free limitations](https://render.com/docs/free), [Flask deployment](https://render.com/docs/deploy-flask), and [Blueprint specification](https://render.com/docs/blueprint-spec).

## Option A — Fresh production database

1. Push this repository to a private GitHub or GitLab repository.
2. In Render, choose **New → Blueprint** and connect the repository. Render reads the root `render.yaml`.
3. Review the generated service before applying it:
   - Runtime: Python
   - Region: Singapore
   - Plan: `0.5c-512mb`
   - Instances: 1
   - Disk mount: `/var/data`, 1 GB
   - Health check: `/healthz`
4. Enter strong private values for the four prompted variables:
   - `INITIAL_ADMIN_USERNAME`
   - `INITIAL_ADMIN_EMAIL`
   - `INITIAL_ADMIN_NAME`
   - `INITIAL_ADMIN_PASSWORD` (at least 10 characters with letters and numbers)
5. Deploy. The start command runs `python seed.py` before Gunicorn. It creates the schema and first Administrator only when no Administrator exists. It never creates demo Staff or Resident users.
6. Open `https://<service-name>.onrender.com/healthz`. Expected response:

   ```json
   {"database":"sqlite","status":"ok"}
   ```

7. Log in using the Administrator secret values, change the password if operational policy requires it, then create Staff accounts from the protected Users page.

## Option B — Preserve the records migrated from MariaDB

The local migration has already created and validated:

`barangay_system/instance/minante.db`

Current migrated snapshot: 13 tables, 3 users, 6 Permit types, 3 Event categories, 1 Blotter case, 8 Notifications, 89 Activity Logs, one active Administrator, zero foreign-key violations, and SQLite integrity result `ok`.

The file is intentionally ignored by Git because it can contain passwords hashes, personal data, complaints, and audit information. Transfer it privately:

1. Deploy the Render Blueprint as in Option A, but do not invite users or accept production submissions yet.
2. Enable Render maintenance mode if available.
3. Configure Render SSH and transfer the local file with SFTP/SCP to a **new filename**, for example `/var/data/minante-imported.db`. Do not overwrite the database file currently open by Gunicorn.
4. In Render, change `SQLITE_DB_PATH` to `/var/data/minante-imported.db`. If the Blueprint controls this value, update the same value in `render.yaml`, push, and sync the Blueprint.
5. Redeploy/restart the service. `seed.py` validates/preserves the migrated Administrator and applies only additive initialization.
6. Verify `/healthz`, Administrator login, record counts, an existing record detail, a PDF receipt, and an upload before disabling maintenance mode.
7. Keep the unused bootstrap database until the imported database has passed backup and restore validation; then remove it through a controlled maintenance procedure.

## Environment values used in production

| Key | Production value/purpose |
|---|---|
| `PYTHON_VERSION` | `3.13.15` |
| `DATA_DIR` | `/var/data` |
| `SQLITE_DB_PATH` | `/var/data/minante.db` (or the imported filename during migration) |
| `SECRET_KEY` | Generated privately by Render |
| `SESSION_COOKIE_SECURE` | `1` |
| `FLASK_DEBUG` | `0` |
| `INITIAL_ADMIN_*` | Private first-start values; never put them in source code |

Do not configure `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, or `DB_NAME`. The running application rejects a non-SQLite `DATABASE_URL`.

## Updating the deployed application

1. Make and test changes locally.
2. Run:

   ```powershell
   cd barangay_system
   python -m py_compile config.py models.py migration.py app.py seed.py
   python -m unittest discover -s tests -v
   python tests/validate_sqlite.py
   python backup_sqlite.py
   ```

3. Push to the linked branch. `autoDeployTrigger: commit` tells Render to deploy the commit automatically.
4. Render stops the disk-attached instance, runs `python seed.py`, and starts the updated single Gunicorn worker.
5. Confirm `/healthz`, login, dashboard values, record creation, and one uploaded document after deployment.

If the build or startup fails, inspect Render logs and leave the existing SQLite file untouched. Fix the code/configuration and redeploy; never delete or recreate `/var/data` as a troubleshooting shortcut.

## Backup and recovery

Run from the Render Shell before major changes:

```bash
python backup_sqlite.py
```

The script uses SQLite's online backup API, validates the result with `PRAGMA integrity_check`, and writes it under `/var/data/backups`. Download important backups to a separate protected location because a backup on the same disk does not protect against complete disk loss. Render also creates daily disk snapshots, but application-consistent downloaded backups should still be retained and periodically restore-tested.

## Final production acceptance checklist

- [ ] The service has a Persistent Disk mounted at `/var/data`.
- [ ] The plan is paid and the service has exactly one instance.
- [ ] Gunicorn runs one worker.
- [ ] `/healthz` returns HTTP 200 and identifies SQLite.
- [ ] `PRAGMA foreign_keys=1`, journal mode is `wal`, and integrity is `ok`.
- [ ] Exactly one intended permanent Administrator can log in.
- [ ] No demo Staff or Resident accounts were created.
- [ ] Permit, Event, Blotter, Schedule, Notification, Report, and Activity Log data survive a restart.
- [ ] Uploaded files survive a restart and are beneath `/var/data/uploads`.
- [ ] A verified database backup has been downloaded off the Render disk.
- [ ] The app remains at one instance; autoscaling is disabled.
