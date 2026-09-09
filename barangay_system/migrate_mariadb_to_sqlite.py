"""One-time, non-destructive migration from MariaDB to the SQLite deployment.

The MariaDB source is read-only and the SQLite target must contain no rows.
Primary keys, password hashes, references, timestamps, and audit history are
preserved. Credentials are read from environment variables and never printed.

Preferred source variables are MARIADB_SOURCE_HOST, MARIADB_SOURCE_PORT,
MARIADB_SOURCE_USER, MARIADB_SOURCE_PASSWORD, and MARIADB_SOURCE_DB. The legacy
DB_* names remain accepted only by this migration utility.
"""
import os
from datetime import time, timedelta

from dotenv import dotenv_values

try:
    import pymysql
except ImportError as exc:  # pragma: no cover - deployment does not install it
    raise RuntimeError(
        "Install migration dependencies first: pip install -r requirements-migration.txt"
    ) from exc

from app import app
from migration import upgrade_legacy_schema
from models import User, db


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PRIVATE_ENV = dotenv_values(os.path.join(BASE_DIR, ".env"))
TABLES = (
    "users",
    "permit_types",
    "event_categories",
    "system_settings",
    "permit_applications",
    "event_requests",
    "blotter_cases",
    "schedules",
    "notifications",
    "activity_logs",
    "reports",
    "password_reset_tokens",
    "announcements",
)


def source_value(preferred, legacy, default=None):
    return (
        os.environ.get(preferred)
        or PRIVATE_ENV.get(preferred)
        or os.environ.get(legacy)
        or PRIVATE_ENV.get(legacy)
        or default
    )


def normalize_value(value):
    # PyMySQL may represent a TIME column as timedelta. SQLAlchemy's SQLite Time
    # adapter expects datetime.time instead.
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds()) % (24 * 60 * 60)
        return time(seconds // 3600, (seconds % 3600) // 60, seconds % 60)
    return value


def migrate():
    source = pymysql.connect(
        host=source_value("MARIADB_SOURCE_HOST", "DB_HOST", "127.0.0.1"),
        port=int(source_value("MARIADB_SOURCE_PORT", "DB_PORT", "3306")),
        user=source_value("MARIADB_SOURCE_USER", "DB_USER"),
        password=source_value("MARIADB_SOURCE_PASSWORD", "DB_PASSWORD"),
        database=source_value("MARIADB_SOURCE_DB", "DB_NAME", "minante_db"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
    )
    counts = {}
    try:
        with source.cursor() as cursor, app.app_context():
            if db.engine.dialect.name != "sqlite":
                raise RuntimeError("Migration target is not SQLite; refusing to continue.")

            upgrade_legacy_schema()
            populated = {
                table: db.session.execute(db.select(db.func.count()).select_from(db.metadata.tables[table])).scalar_one()
                for table in TABLES
                if table in db.metadata.tables
            }
            populated = {table: count for table, count in populated.items() if count}
            if populated:
                raise RuntimeError(
                    "SQLite target is not empty; migration refused to prevent duplicate or overwritten records: "
                    + ", ".join(f"{table}={count}" for table, count in populated.items())
                )

            cursor.execute("SHOW TABLES")
            source_tables = {next(iter(row.values())) for row in cursor.fetchall()}

            for table_name in TABLES:
                if table_name not in source_tables or table_name not in db.metadata.tables:
                    counts[table_name] = 0
                    continue
                cursor.execute(f"SELECT * FROM `{table_name}`")
                rows = cursor.fetchall()
                target_table = db.metadata.tables[table_name]
                columns = set(target_table.columns.keys())
                values = [
                    {key: normalize_value(value) for key, value in row.items() if key in columns}
                    for row in rows
                ]
                if values:
                    db.session.execute(target_table.insert(), values)
                counts[table_name] = len(values)

            db.session.flush()
            active_admin = User.query.filter_by(role="admin", is_active=True).filter(
                User.password_hash.isnot(None), User.password_hash != ""
            ).first()
            if not active_admin:
                raise RuntimeError("Permanent Administrator validation failed; rolling back.")

            violations = db.session.execute(db.text("PRAGMA foreign_key_check")).all()
            if violations:
                raise RuntimeError(f"SQLite foreign-key validation failed: {violations[:5]}")

            for table_name, expected in counts.items():
                if table_name not in db.metadata.tables:
                    continue
                actual = db.session.execute(
                    db.select(db.func.count()).select_from(db.metadata.tables[table_name])
                ).scalar_one()
                if actual != expected:
                    raise RuntimeError(
                        f"Row-count validation failed for {table_name}: source={expected}, target={actual}"
                    )

            db.session.commit()
            target_path = app.config["SQLITE_DB_PATH"]
    except Exception:
        with app.app_context():
            db.session.rollback()
        raise
    finally:
        source.rollback()
        source.close()

    print(f"Migration committed successfully to {target_path}.")
    for table_name, count in counts.items():
        print(f"{table_name}: {count}")


if __name__ == "__main__":
    migrate()
