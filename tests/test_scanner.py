from pathlib import Path
from tools.backend_catalog.scanner import scan_project


def test_scanner_resolves_oip_patterns():
    root = Path(__file__).parent / 'fixtures'
    catalog = scan_project(root)
    paths = {(r.method, p) for r in catalog.routes for p in r.full_paths}
    assert ('GET', '/api/shift/current') in paths
    assert ('GET', '/api/daily-operations/workcells/{workcell}/shift-activity') in paths

    symbols = {s.symbol_id: s for s in catalog.symbols}
    service = symbols['app.applications.daily_operations.services.daily_operations_services.get_workcell_shift_activity']
    resolved = {c.resolved for c in service.calls}
    assert 'app.domains.shift.shift_services.ShiftContextService.get_current_shift' in resolved
    assert 'app.applications.daily_operations.repos.shift_activity_repo.fetch_workcell_shift_activity' in resolved

    repo = symbols['app.applications.daily_operations.repos.shift_activity_repo.fetch_workcell_shift_activity']
    assert repo.sql_files == ['workcell_shift_activity.sql']
    assert 'wcc.dbo.entity_state_history' in repo.tables
    assert 'wcc.dbo.entity' in repo.tables
