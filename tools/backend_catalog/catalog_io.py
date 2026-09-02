from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import CallRecord, CatalogRecord, ParameterRecord, RouteRecord, SymbolRecord


def catalog_from_dict(data: dict[str, Any]) -> CatalogRecord:
    symbols: list[SymbolRecord] = []
    for raw in data.get("symbols", []):
        symbols.append(
            SymbolRecord(
                symbol_id=raw["symbol_id"],
                module=raw["module"],
                name=raw["name"],
                qualname=raw["qualname"],
                kind=raw["kind"],
                layer=raw.get("layer"),
                role=raw.get("role"),
                public=bool(raw.get("public")),
                parameters=[ParameterRecord(**item) for item in raw.get("parameters", [])],
                return_annotation=raw.get("return_annotation"),
                docstring=raw.get("docstring"),
                calls=[CallRecord(**item) for item in raw.get("calls", [])],
                called_by=list(raw.get("called_by", [])),
                sql_files=list(raw.get("sql_files", [])),
                sql_file_candidates={
                    str(key): list(value)
                    for key, value in (raw.get("sql_file_candidates") or {}).items()
                },
                tables=list(raw.get("tables", [])),
                source_file=raw.get("source_file", ""),
                source_line=int(raw.get("source_line", 0)),
            )
        )

    routes = [RouteRecord(**raw) for raw in data.get("routes", [])]
    return CatalogRecord(
        catalog_version=str(data.get("catalog_version", "unknown")),
        source_fingerprint=str(data.get("source_fingerprint", "")),
        scan_root=str(data.get("scan_root", "")),
        symbols=symbols,
        routes=routes,
        warnings=list(data.get("warnings", [])),
    )


def load_catalog_json(path: Path) -> CatalogRecord:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Backend catalog JSON root must be an object")
    return catalog_from_dict(data)
