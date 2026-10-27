"""Generate route inventory and measured timings against synthetic SQLite records."""
import ast
import inspect
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_system as baseline
from app import app
from models import db, PermitApplication
from sqlalchemy import text, event


def main():
    fixture=baseline.SystemAcceptanceTests();fixture.setUpClass();fixture.setUp()
    output=Path('audit-artifacts');output.mkdir(exist_ok=True)
    try:
        rows=['# Route inventory','', '| Methods | Route | Handler | Access checks |','|---|---|---|---|']
        for rule in sorted(app.url_map.iter_rules(),key=lambda r:r.rule):
            handler=inspect.unwrap(app.view_functions[rule.endpoint])
            source=inspect.getsource(handler) if rule.endpoint!='static' else ''
            checks=[]
            for marker in ['login_required','permission_required','administrator()','operators()','staff_or_admin()','ensure_ownership(','application_access(','can_record_payment(','permit_processor_allowed(']:
                if marker in source:checks.append(marker.rstrip('('))
            rows.append('| '+', '.join(sorted(rule.methods-{'HEAD','OPTIONS'}))+' | `'+rule.rule+'` | '+rule.endpoint+' | '+', '.join(checks)+' |')
        (output/'ROUTE_INVENTORY.md').write_text('\n'.join(rows),encoding='utf-8')
        compiled=[]
        for path in Path('.').rglob('*.py'):
            if 'instance' in path.parts or 'audit-artifacts' in path.parts:continue
            ast.parse(path.read_text(encoding='utf-8-sig'));compiled.append(str(path))
        for name in app.jinja_env.list_templates():app.jinja_env.get_template(name)
        with app.app_context():
            db.session.add_all([PermitApplication(applicant_id=fixture.ids['resident'],permit_type_id=fixture.ids['permit_type'],purpose='Synthetic performance fixture',status='Pending' if i%2 else 'Approved',fee_at_submission=50) for i in range(5000)])
            db.session.commit()
            plan=db.session.execute(text('EXPLAIN QUERY PLAN SELECT id FROM permit_applications WHERE applicant_id=:id ORDER BY application_date DESC LIMIT 30'),{'id':fixture.ids['resident']}).all()
            index_plan=[str(row) for row in plan]
        fixture.auth('admin')
        measured={}
        for url in ['/dashboard','/front-desk?q=Resident','/permits','/events','/activity-logs','/notifications','/reports?report_type=permits']:
            durations=[];counts=[]
            for _ in range(3):
                queries=[]
                def track(*args):queries.append(1)
                with app.app_context():engine=db.engine
                event.listen(engine,'before_cursor_execute',track)
                try:
                    started=time.perf_counter();response=fixture.client.get(url)
                    durations.append(round((time.perf_counter()-started)*1000,2));counts.append(len(queries))
                    assert response.status_code==200,(url,response.status_code)
                finally:event.remove(engine,'before_cursor_execute',track)
            measured[url]={'median_ms':statistics.median(durations),'samples_ms':durations,'query_counts':counts}
        started=time.perf_counter();response=fixture.post('/reports/export/pdf',{'report_type':'permits'})
        assert response.status_code==200
        measured['pdf_export']={'ms':round((time.perf_counter()-started)*1000,2),'bytes':len(response.data)}
        data={'synthetic_permits':5000,'timings':measured,'permit_query_plan':index_plan,'python_files_parsed':compiled,'template_count':len(app.jinja_env.list_templates()),'route_count':len(list(app.url_map.iter_rules()))}
        (output/'performance-results.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
        print(json.dumps(data,indent=2))
    finally:fixture.tearDownClass()


if __name__=='__main__':main()
