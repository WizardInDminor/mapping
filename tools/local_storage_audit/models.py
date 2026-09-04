from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ColumnInfo:
    name: str
    declared_type: str | None
    not_null: bool
    default: str | None
    primary_key_position: int


@dataclass(slots=True)
class ForeignKeyInfo:
    id: int
    seq: int
    target_table: str
    from_column: str | None
    to_column: str | None
    on_update: str | None
    on_delete: str | None


@dataclass(slots=True)
class IndexInfo:
    name: str
    unique: bool
    origin: str | None
    partial: bool
    columns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TableInfo:
    name: str
    sql: str | None
    columns: list[ColumnInfo] = field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)
    indexes: list[IndexInfo] = field(default_factory=list)
    row_count: int | None = None
    row_count_error: str | None = None


@dataclass(slots=True)
class SchemaObject:
    type: str
    name: str
    table_name: str | None
    sql: str | None


@dataclass(slots=True)
class DatabaseInfo:
    path: str
    relative_path: str
    scope: str
    size_bytes: int
    sqlite_header: bool
    readable: bool
    error: str | None = None
    user_version: int | None = None
    schema_version: int | None = None
    tables: list[TableInfo] = field(default_factory=list)
    views: list[SchemaObject] = field(default_factory=list)
    triggers: list[SchemaObject] = field(default_factory=list)


@dataclass(slots=True)
class SourceEvidence:
    kind: str
    path: str
    line: int
    symbol: str | None = None
    table: str | None = None
    db_literal: str | None = None
    detail: str | None = None
    declared_columns: list[str] = field(default_factory=list)
    local_storage_hint: bool = False
    runtime_invoked: bool = False
    runtime_reason: str | None = None


@dataclass(slots=True)
class SourceScan:
    evidence: list[SourceEvidence] = field(default_factory=list)


@dataclass(slots=True)
class TableAudit:
    database_path: str
    table: str
    status: str
    status_reason: str
    consumers: list[SourceEvidence] = field(default_factory=list)
    creators: list[SourceEvidence] = field(default_factory=list)
    missing_declared_columns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MissingTableAudit:
    table: str
    status: str
    reason: str
    consumers: list[SourceEvidence] = field(default_factory=list)
    creators: list[SourceEvidence] = field(default_factory=list)


@dataclass(slots=True)
class AuditReport:
    audit_version: str
    scan_root: str
    safety_mode: str
    databases: list[DatabaseInfo]
    table_audits: list[TableAudit]
    missing_tables: list[MissingTableAudit]
    source_scan: SourceScan

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
