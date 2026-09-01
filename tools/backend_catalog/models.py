from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ParameterRecord:
    name: str
    annotation: str | None = None
    default: str | None = None
    kind: str = "positional_or_keyword"


@dataclass(slots=True)
class CallRecord:
    raw: str
    resolved: str | None = None
    line: int | None = None


@dataclass(slots=True)
class RouteRecord:
    method: str
    local_path: str
    router_symbol: str
    router_prefix: str
    full_paths: list[str]
    response_model: str | None
    handler_symbol: str
    source_file: str
    line: int


@dataclass(slots=True)
class SymbolRecord:
    symbol_id: str
    module: str
    name: str
    qualname: str
    kind: str
    layer: str | None
    role: str | None
    public: bool
    parameters: list[ParameterRecord] = field(default_factory=list)
    return_annotation: str | None = None
    docstring: str | None = None
    calls: list[CallRecord] = field(default_factory=list)
    called_by: list[str] = field(default_factory=list)
    sql_files: list[str] = field(default_factory=list)
    sql_file_candidates: dict[str, list[str]] = field(default_factory=dict)
    tables: list[str] = field(default_factory=list)
    source_file: str = ""
    source_line: int = 0


@dataclass(slots=True)
class CatalogRecord:
    catalog_version: str
    source_fingerprint: str
    scan_root: str
    symbols: list[SymbolRecord]
    routes: list[RouteRecord]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
