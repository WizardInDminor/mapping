from pathlib import Path

from tools.backend_catalog.capabilities import load_capabilities, validate_capabilities
from tools.backend_catalog.catalog_io import catalog_from_dict
from tools.backend_catalog.render_capabilities import render_capability_markdown
from tools.backend_catalog.scanner import scan_project


def _write_registry(path: Path) -> None:
    path.write_text(
        '''capability_version: "0.2.0"
capabilities:
  - id: shift.current
    name: Current Shift Context
    category: Shift & Time Context
    status: canonical
    question: What operational shift owns a given datetime?
    entrypoints:
      - app.domains.shift.router.get_current_shift
    use_when:
      - A feature needs canonical shift boundaries.
    related:
      - daily_operations.workcell_shift_activity
  - id: daily_operations.workcell_shift_activity
    name: Workcell Shift Activity
    category: Daily Operations
    status: canonical
    question: What operational events occurred in a workcell during a shift?
    entrypoints:
      - app.applications.daily_operations.routes.daily_operations_routes.get_workcell_shift_activity
    related:
      - shift.current
''',
        encoding='utf-8',
    )


def test_capability_registry_validates_and_renders(tmp_path: Path):
    root = Path(__file__).parent / 'fixtures'
    catalog = scan_project(root)
    registry_path = tmp_path / 'capabilities.yaml'
    _write_registry(registry_path)

    registry = load_capabilities(registry_path)
    validation = validate_capabilities(registry, catalog)
    assert validation.errors == []

    markdown = render_capability_markdown(registry, catalog)
    assert '# Backend Capability Catalog' in markdown
    assert 'GET /api/shift/current' in markdown
    assert 'GET /api/daily-operations/workcells/{workcell}/shift-activity' in markdown
    assert 'daily_operations_services.get_workcell_shift_activity' in markdown
    assert 'shift_activity_repo.fetch_workcell_shift_activity' in markdown
    assert 'workcell_shift_activity.sql' in markdown
    assert 'wcc.dbo.entity_state_history' in markdown


def test_capability_registry_rejects_missing_entrypoint(tmp_path: Path):
    root = Path(__file__).parent / 'fixtures'
    catalog = scan_project(root)
    registry_path = tmp_path / 'capabilities.yaml'
    registry_path.write_text(
        '''capability_version: "0.2.0"
capabilities:
  - id: broken.example
    name: Broken Example
    category: Test
    status: canonical
    question: Does validation catch stale semantic references?
    entrypoints:
      - app.does.not.exist
''',
        encoding='utf-8',
    )
    registry = load_capabilities(registry_path)
    validation = validate_capabilities(registry, catalog)
    assert any('entrypoint does not exist' in error for error in validation.errors)


def test_catalog_json_round_trip_shape():
    root = Path(__file__).parent / 'fixtures'
    catalog = scan_project(root)
    restored = catalog_from_dict(catalog.to_dict())
    assert restored.catalog_version == catalog.catalog_version
    assert len(restored.symbols) == len(catalog.symbols)
    assert len(restored.routes) == len(catalog.routes)
    service = next(
        symbol for symbol in restored.symbols
        if symbol.symbol_id.endswith('daily_operations_services.get_workcell_shift_activity')
    )
    assert any(call.resolved and call.resolved.endswith('fetch_workcell_shift_activity') for call in service.calls)
