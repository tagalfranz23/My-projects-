"""Production-safe initialization: never creates demo Staff or Resident users."""
import os
from app import app
from models import db, User, PermitType, EventCategory
from migration import upgrade_legacy_schema

def initialize():
    with app.app_context():
        upgrade_legacy_schema()
        admin=User.query.filter_by(role="admin").order_by(User.id).first()
        if admin: print(f"Permanent administrator preserved (ID {admin.id}).")
        else:
            username=os.environ.get("INITIAL_ADMIN_USERNAME"); email=os.environ.get("INITIAL_ADMIN_EMAIL"); password=os.environ.get("INITIAL_ADMIN_PASSWORD")
            if not all((username,email,password)): raise RuntimeError("No administrator exists. Set INITIAL_ADMIN_USERNAME, INITIAL_ADMIN_EMAIL, and INITIAL_ADMIN_PASSWORD.")
            admin=User(username=username,email=email.lower(),full_name=os.environ.get("INITIAL_ADMIN_NAME","System Administrator"),role="admin"); admin.set_password(password); db.session.add(admin)
        for name in ("Barangay Clearance","Business Clearance","Certificate of Residency"):
            if not PermitType.query.filter_by(name=name).first(): db.session.add(PermitType(name=name))
        for name in ("Community Event","Sports Activity","Public Assembly"):
            if not EventCategory.query.filter_by(name=name).first(): db.session.add(EventCategory(name=name))
        db.session.commit(); print("Catalogs initialized. No demo Staff or Resident accounts were created.")

if __name__=="__main__": initialize()
