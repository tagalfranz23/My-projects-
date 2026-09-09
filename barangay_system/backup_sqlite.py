"""Create a consistent SQLite backup while the application may be running."""
from datetime import datetime, timezone
import os
import sqlite3

from config import Config


def backup():
    source_path = Config.SQLITE_DB_PATH
    if not os.path.isfile(source_path):
        raise RuntimeError(f"SQLite database was not found: {source_path}")

    backup_dir = os.path.join(Config.DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = os.path.join(backup_dir, f"minante-{timestamp}.db")
    temporary_path = final_path + ".partial"

    source = sqlite3.connect(source_path, timeout=30)
    target = sqlite3.connect(temporary_path)
    try:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if str(result).lower() != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
        target.close()
        target = None
        os.replace(temporary_path, final_path)
    finally:
        source.close()
        if target is not None:
            target.close()
        if os.path.exists(temporary_path):
            os.remove(temporary_path)

    print(f"SQLite backup created and verified: {final_path}")


if __name__ == "__main__":
    backup()
