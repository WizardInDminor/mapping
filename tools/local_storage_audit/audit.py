from __future__ import annotations

from pathlib import Path

from .discovery import discover_sqlite_files
from .models import AuditReport
from .reconcile import reconcile
from .source_scanner import scan_source
from .sqlite_inspector import inspect_sqlite_database


AUDIT_VERSION = "0.1.0"


def run_audit(
    root: Path,
    *,
    include_row_counts: bool = True,
) -> AuditReport:
    root = root.resolve()

    databases = [
        inspect_sqlite_database(
            path,
            root,
            include_row_counts=include_row_counts,
        )
        for path in discover_sqlite_files(root)
    ]

    source_scan = scan_source(root)
    table_audits, missing_tables = reconcile(databases, source_scan)

    return AuditReport(
        audit_version=AUDIT_VERSION,
        scan_root=str(root),
        safety_mode=(
            "SQLite URI mode=ro + PRAGMA query_only=ON; "
            "static source analysis; no application imports"
        ),
        databases=databases,
        table_audits=table_audits,
        missing_tables=missing_tables,
        source_scan=source_scan,
    )
