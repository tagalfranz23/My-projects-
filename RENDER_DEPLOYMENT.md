# Render Deployment — SQLite

This project is SQLite-only. The Render Free plan is appropriate only for an evaluation or demonstration because its filesystem is ephemeral: a restart, redeploy, or spin-down can erase both the SQLite database and private uploads.

For production, use a Render plan with a persistent disk (or another host with persistent storage), configure `DATA_DIR` and `SQLITE_DB_PATH` beneath that disk, and run exactly one service instance. Keep one Gunicorn worker so SQLite remains a single-node datastore.

## Deploy

1. Push the tested repository revision to GitHub.
2. In Render, create a Blueprint from the repository.
3. Set a strong private `SECRET_KEY` and all `INITIAL_ADMIN_*` values only if the database is new.
4. For a persistent deployment, mount a disk and set `DATA_DIR` and `SQLITE_DB_PATH` to locations on that disk. Do not use `/tmp` for production records.
5. Deploy, then open `/healthz`, sign in as the intended Administrator, and verify real records after a restart.

Before any update, copy the SQLite database while the application is stopped or use the included backup process. The startup migration is additive: it creates missing fields/tables and preserves existing users, roles, and historical transactions.

Private ID uploads must remain outside static folders. The system serves them only through authorization checks; do not expose the identification directory through a web-server static mapping.
