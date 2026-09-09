"""Non-destructive, idempotent SQLite compatibility migration.

Existing users (especially the permanent administrator) are never deleted or recreated.
"""
from sqlalchemy import inspect, text
from models import db

ADDITIONS = {
 "users": {"updated_at":"DATETIME",},
 "permit_types":{"created_at":"DATETIME","updated_at":"DATETIME"},
 "permit_applications":{"created_at":"DATETIME","updated_at":"DATETIME","reviewed_by":"INTEGER","attachment_path":"VARCHAR(255)"},
 "blotter_cases":{"created_at":"DATETIME","supporting_document_path":"VARCHAR(255)"},
 "notifications":{"user_id":"INTEGER","title":"VARCHAR(180) DEFAULT 'System notification'","channel":"VARCHAR(20) DEFAULT 'sms'","read_at":"DATETIME","created_at":"DATETIME"},
 "activity_logs":{"actor_role":"VARCHAR(20)","resource":"VARCHAR(50) DEFAULT 'system'","target_reference":"VARCHAR(80)","user_agent":"VARCHAR(255)"},
}

def upgrade_legacy_schema():
    try:
        inspector=inspect(db.engine); tables=set(inspector.get_table_names())
        for table, columns in ADDITIONS.items():
            if table not in tables: continue
            existing={c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    quote=db.engine.dialect.identifier_preparer.quote
                    db.session.execute(text(f"ALTER TABLE {quote(table)} ADD COLUMN {quote(name)} {ddl}"))
        db.session.commit()
        db.create_all()
        # Normalize legacy workflow labels without losing records.
        db.session.execute(text("UPDATE blotter_cases SET status='Filed' WHERE status='Pending'"))
        db.session.execute(text("UPDATE blotter_cases SET status='For Investigation' WHERE status='Ongoing'"))
        db.session.execute(text("UPDATE blotter_cases SET status='Closed' WHERE status='Dismissed'"))
        db.session.execute(text("UPDATE permit_applications SET status='Completed' WHERE status='Released'"))
        db.session.execute(text("UPDATE notifications SET user_id=recipient_user_id WHERE user_id IS NULL"))
        db.session.execute(text("UPDATE notifications SET channel='sms' WHERE channel IS NULL"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
