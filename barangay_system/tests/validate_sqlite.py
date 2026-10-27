"""SQLite restart/integrity check using a fresh isolated test database only."""
import os
import subprocess
import sys
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from app import app
from models import (db, utcnow, User, PermitApplication, EventRequest, BlotterCase,
    Schedule, Notification, ActivityLog, Report, SystemSetting)
from sqlalchemy import text


def main():
    fixture=baseline.SystemAcceptanceTests();fixture.setUpClass();fixture.setUp()
    try:
        with app.app_context():
            assert db.engine.dialect.name=='sqlite'
            assert db.session.execute(text('PRAGMA foreign_keys')).scalar()==1
            assert db.session.execute(text('PRAGMA journal_mode')).scalar().lower()=='wal'
            resident=fixture.ids['resident']
            permit=PermitApplication(applicant_id=resident,permit_type_id=fixture.ids['permit_type'],purpose='Restart test')
            event=EventRequest(requester_id=resident,event_name='Restart test',event_type='Community Activity',description='Synthetic',proposed_date=date.today()+timedelta(days=2),proposed_location='Test venue',expected_attendees=2)
            blotter=BlotterCase(complainant_id=resident,respondent_name='Synthetic',incident_type='Test',incident_date=utcnow(),incident_location='Test location',narrative='Synthetic')
            db.session.add_all([permit,event,blotter]);db.session.flush()
            schedule=Schedule(related_type='permit',related_id=permit.id,requested_by=resident,scheduled_datetime=utcnow()+timedelta(days=1),purpose='Restart test')
            notification=Notification(user_id=resident,title='Restart test',message='Synthetic',channel='in_app')
            activity=ActivityLog(user_id=resident,action='SQLITE_VALIDATION',resource='system')
            report=Report(generated_by=fixture.ids['admin'],report_type='overall',format='docx')
            setting=SystemSetting(key='__sqlite_validation__',value='synthetic')
            db.session.add_all([schedule,notification,activity,report,setting]);db.session.commit()
            ids=[resident,permit.id,event.id,blotter.id,schedule.id,notification.id,activity.id,report.id,setting.id]
        fixture.auth('resident')
        response=fixture.client.get(f'/acknowledgments/permit/{ids[1]}.docx')
        assert response.status_code==200 and response.data.startswith(b'PK')
        probe=subprocess.run([sys.executable,'tests/sqlite_restart_probe.py',*map(str,ids)],check=True,capture_output=True,text=True,env=os.environ.copy())
        assert 'SQLITE_RESTART_PROBE_OK' in probe.stdout
        with app.app_context():
            assert db.session.execute(text('PRAGMA integrity_check')).scalar()=='ok'
            assert db.session.execute(text('PRAGMA foreign_key_check')).all()==[]
        print('SQLITE_VALIDATION_OK: isolated database, pragmas, DOCX, separate-process restart, integrity')
    finally:fixture.tearDownClass()


if __name__=='__main__':main()
