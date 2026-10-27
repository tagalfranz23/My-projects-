"""Inspect configured SQLite storage without migrations, app startup, or writes.

Only schema, integrity results and aggregate inconsistency counts are printed.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Config
from sqlalchemy.engine import make_url


def main():
    database = make_url(Config.SQLALCHEMY_DATABASE_URI).database
    if not database or database == ':memory:':
        raise RuntimeError('The configured database is not a persistent SQLite file.')
    path = Path(database)
    if not path.is_absolute():
        # Flask-SQLAlchemy resolves relative SQLite URLs beneath app.instance_path.
        path = Path(__file__).resolve().parents[1] / 'instance' / path
    if not path.is_file():
        raise RuntimeError('The configured SQLite database file does not exist.')
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        result = {
            'database_file': str(path.resolve()),
            'integrity': connection.execute('PRAGMA integrity_check').fetchone()[0],
            'foreign_key_violations': len(connection.execute('PRAGMA foreign_key_check').fetchall()),
            'tables': connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0],
            'indexes': connection.execute("SELECT count(*) FROM sqlite_master WHERE type='index'").fetchone()[0],
        }
        checks = {
            'legacy_payment_flags_without_payment': '''SELECT count(*) FROM permit_applications p
                WHERE p.payment_confirmed_at IS NOT NULL AND p.fee_at_submission > 0
                AND NOT EXISTS (SELECT 1 FROM payments x WHERE x.permit_id=p.id)''',
            'released_without_signature': '''SELECT count(*) FROM permit_applications
                WHERE released_at IS NOT NULL AND signed_at IS NULL''',
            'multiple_active_households': '''SELECT count(*) FROM (SELECT resident_id FROM household_members
                WHERE membership_status='Active' GROUP BY resident_id HAVING count(*)>1)''',
        }
        for name, query in checks.items():
            try:
                result[name] = connection.execute(query).fetchone()[0]
            except sqlite3.OperationalError as error:
                result[name] = 'Not checked: ' + str(error)
        paths = connection.execute('SELECT valid_id_file_path FROM resident_profiles WHERE valid_id_file_path IS NOT NULL').fetchall()
        result['missing_id_files'] = sum(not os.path.isfile(os.path.join(Config.IDENTIFICATION_FOLDER, os.path.basename(value))) for (value,) in paths)
        print(json.dumps(result, indent=2))
    finally:
        connection.close()


if __name__ == '__main__':
    main()
