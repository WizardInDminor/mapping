from __future__ import annotations

from collections import defaultdict

from .models import (
    DatabaseInfo,
    MissingTableAudit,
    SourceEvidence,
    SourceScan,
    TableAudit,
)


def _table_key(value: str | None) -> str:
    return (value or "").strip().strip("`\"[]").lower()


def _is_physical_local_table_name(name: str) -> bool:
    # SQLite table names are normally unqualified. Exclude obvious MSSQL-style
    # references that leaked into local-storage source files.
    return bool(name) and "." not in name and name.lower() != "database"


def reconcile(
    databases: list[DatabaseInfo],
    source_scan: SourceScan,
) -> tuple[list[TableAudit], list[MissingTableAudit]]:
    creators: dict[str, list[SourceEvidence]] = defaultdict(list)
    consumers: dict[str, list[SourceEvidence]] = defaultdict(list)

    for evidence in source_scan.evidence:
        key = _table_key(evidence.table)
        if not key or not _is_physical_local_table_name(key):
            continue
        if evidence.kind == "create_table" and evidence.local_storage_hint:
            creators[key].append(evidence)
        elif evidence.kind == "table_reference" and evidence.local_storage_hint:
            consumers[key].append(evidence)

    observed: dict[str, list[tuple[DatabaseInfo, object]]] = defaultdict(list)
    for database in databases:
        if not database.readable:
            continue
        for table in database.tables:
            observed[_table_key(table.name)].append((database, table))

    audits: list[TableAudit] = []

    for key, occurrences in observed.items():
        table_creators = creators.get(key, [])
        table_consumers = consumers.get(key, [])

        for database, table in occurrences:
            actual_columns = {column.name.lower() for column in table.columns}
            declared_columns: set[str] = set()
            for creator in table_creators:
                declared_columns.update(column.lower() for column in creator.declared_columns)

            missing_declared = sorted(declared_columns - actual_columns)

            if missing_declared:
                status = "DRIFT"
                reason = (
                    "Observed schema is missing column(s) declared by discovered "
                    "CREATE TABLE evidence."
                )
            elif not table_consumers:
                status = "ORPHANED"
                reason = "Observed local table has no current SQLite consumer detected."
            elif not table_creators:
                status = "UNOWNED"
                reason = (
                    "Table is consumed by local-storage code, but no schema creation "
                    "path was detected."
                )
            elif any(creator.runtime_invoked for creator in table_creators):
                status = "PORTABLE"
                reason = (
                    "Schema creation is present and static analysis found a runtime "
                    "initialization path."
                )
            elif all(
                creator.path.replace("\\", "/").startswith("scripts/")
                or creator.symbol is None
                for creator in table_creators
            ):
                status = "MANUAL"
                reason = (
                    "Schema creation exists only in standalone scripts/SQL; no runtime "
                    "bootstrap path was proven."
                )
            else:
                status = "UNKNOWN"
                reason = (
                    "Schema creation exists, but static analysis could not prove that "
                    "clean application startup invokes it."
                )

            audits.append(
                TableAudit(
                    database_path=database.relative_path,
                    table=table.name,
                    status=status,
                    status_reason=reason,
                    consumers=sorted(
                        table_consumers,
                        key=lambda item: (item.path, item.line),
                    ),
                    creators=sorted(
                        table_creators,
                        key=lambda item: (item.path, item.line),
                    ),
                    missing_declared_columns=missing_declared,
                )
            )

    missing: list[MissingTableAudit] = []
    all_expected = set(creators) | set(consumers)
    for key in sorted(all_expected - set(observed)):
        table_consumers = consumers.get(key, [])
        table_creators = creators.get(key, [])
        # A CREATE-only table absent locally is worth noting, but a consumer is
        # the stronger signal that application behavior currently expects it.
        if table_consumers:
            reason = (
                "Local-storage source code references this table, but it was not found "
                "in any discovered readable SQLite database."
            )
        else:
            reason = (
                "Schema creation code exists for this table, but no discovered readable "
                "SQLite database currently contains it."
            )
        missing.append(
            MissingTableAudit(
                table=key,
                status="MISSING",
                reason=reason,
                consumers=sorted(table_consumers, key=lambda item: (item.path, item.line)),
                creators=sorted(table_creators, key=lambda item: (item.path, item.line)),
            )
        )

    audits.sort(key=lambda item: (item.database_path, item.table.lower()))
    return audits, missing
