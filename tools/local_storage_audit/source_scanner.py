from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from .discovery import DEFAULT_EXCLUDED_DIRS, SQLITE_SUFFIXES
from .models import SourceEvidence, SourceScan


CREATE_TABLE_RE = re.compile(
    r"""
    CREATE\s+TABLE
    (?:\s+IF\s+NOT\s+EXISTS)?
    \s+
    (?P<name>[`"\[\]\w.]+)
    \s*\(
    (?P<body>.*?)
    \)
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

CREATE_INDEX_RE = re.compile(
    r"""
    CREATE\s+(?:UNIQUE\s+)?INDEX
    (?:\s+IF\s+NOT\s+EXISTS)?
    \s+(?P<name>[`"\[\]\w.]+)
    \s+ON\s+(?P<table>[`"\[\]\w.]+)
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

TABLE_REF_RE = re.compile(
    r"""
    \b(?:
        FROM |
        JOIN |
        UPDATE |
        INSERT\s+INTO |
        DELETE\s+FROM |
        REPLACE\s+INTO
    )\s+
    (?P<table>[`"\[\]\w.]+)
    """,
    re.IGNORECASE | re.VERBOSE,
)

DB_LITERAL_RE = re.compile(
    r"""(?P<path>[^'"\s]+?\.(?:db|sqlite|sqlite3))\b""",
    re.IGNORECASE,
)

SQLITE_URL_RE = re.compile(r"sqlite(?:\+\w+)?:///", re.IGNORECASE)

CONSTRAINT_PREFIXES = {
    "PRIMARY",
    "FOREIGN",
    "UNIQUE",
    "CHECK",
    "CONSTRAINT",
}

SQLITE_INTERNAL_TABLES = {
    "sqlite_sequence",
    "sqlite_stat1",
    "sqlite_stat4",
}


@dataclass
class FunctionRecord:
    symbol: str
    path: str
    line: int
    class_name: str | None
    name: str
    calls: list[str] = field(default_factory=list)
    strings: list[tuple[str, int]] = field(default_factory=list)


class _ModuleVisitor(ast.NodeVisitor):
    def __init__(self, module: str, path: str):
        self.module = module
        self.path = path
        self.imports: dict[str, str] = {}
        self.functions: dict[str, FunctionRecord] = {}
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.module_strings: list[tuple[str, int]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.imports[local] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                self.imports[local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        class_name = self.class_stack[-1] if self.class_stack else None
        qualname = f"{class_name}.{node.name}" if class_name else node.name
        symbol = f"{self.module}.{qualname}"
        record = FunctionRecord(
            symbol=symbol,
            path=self.path,
            line=node.lineno,
            class_name=class_name,
            name=node.name,
        )
        self.functions[symbol] = record
        self.function_stack.append(symbol)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            item = (node.value, getattr(node, "lineno", 1))
            if self.function_stack:
                self.functions[self.function_stack[-1]].strings.append(item)
            else:
                self.module_strings.append(item)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.function_stack:
            raw = _call_name(node.func)
            if raw:
                resolved = self._resolve_call(raw, self.function_stack[-1])
                self.functions[self.function_stack[-1]].calls.append(resolved)
        self.generic_visit(node)

    def _resolve_call(self, raw: str, current_symbol: str) -> str:
        current = self.functions[current_symbol]

        if raw.startswith("self.") and current.class_name:
            return f"{self.module}.{current.class_name}.{raw.split('.', 1)[1]}"

        first, *rest = raw.split(".")
        if first in self.imports:
            base = self.imports[first]
            return ".".join([base, *rest]) if rest else base

        if "." not in raw:
            same_module = f"{self.module}.{raw}"
            same_class = (
                f"{self.module}.{current.class_name}.{raw}"
                if current.class_name
                else None
            )
            if same_class and same_class in self.functions:
                return same_class
            return same_module

        return raw


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    return ".".join(relative.parts)


def _strip_identifier(value: str) -> str:
    return value.strip().strip("`\"[]")


def _split_sql_columns(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote_char: str | None = None

    for char in body:
        if quote_char:
            current.append(char)
            if char == quote_char:
                quote_char = None
            continue
        if char in {"'", '"'}:
            quote_char = char
            current.append(char)
            continue
        if char == "(":
            depth += 1
            current.append(char)
            continue
        if char == ")":
            depth = max(0, depth - 1)
            current.append(char)
            continue
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)

    if current:
        parts.append("".join(current).strip())

    columns: list[str] = []
    for part in parts:
        if not part:
            continue
        first = part.split(None, 1)[0].strip("`\"[]")
        if first.upper() in CONSTRAINT_PREFIXES:
            continue
        if re.match(r"^[A-Za-z_][\w$]*$", first):
            columns.append(first)
    return columns


def _is_local_storage_module(
    path: str,
    strings: list[tuple[str, int]],
    imports: dict[str, str] | None = None,
) -> bool:
    normalized = path.replace("\\", "/").lower()
    if "mssql" in normalized:
        return False
    if "sqlite" in normalized:
        return True
    imported_targets = set((imports or {}).values())
    if any(target == "sqlite3" or target.startswith("sqlite3.") for target in imported_targets):
        return True
    for value, _ in strings:
        lowered = value.lower()
        if SQLITE_URL_RE.search(value):
            return True
        if any(suffix in lowered for suffix in SQLITE_SUFFIXES):
            return True
        if "pragma " in lowered:
            return True
    return False


def _extract_string_evidence(
    *,
    value: str,
    line: int,
    path: str,
    symbol: str | None,
    local_storage_hint: bool,
) -> list[SourceEvidence]:
    evidence: list[SourceEvidence] = []

    for match in DB_LITERAL_RE.finditer(value):
        evidence.append(
            SourceEvidence(
                kind="db_path_literal",
                path=path,
                line=line,
                symbol=symbol,
                db_literal=match.group("path"),
                detail=match.group("path"),
                local_storage_hint=True,
            )
        )

    if SQLITE_URL_RE.search(value):
        evidence.append(
            SourceEvidence(
                kind="sqlite_url",
                path=path,
                line=line,
                symbol=symbol,
                detail=value[:300],
                local_storage_hint=True,
            )
        )

    for match in CREATE_TABLE_RE.finditer(value):
        table = _strip_identifier(match.group("name"))
        evidence.append(
            SourceEvidence(
                kind="create_table",
                path=path,
                line=line,
                symbol=symbol,
                table=table,
                detail="CREATE TABLE",
                declared_columns=_split_sql_columns(match.group("body")),
                local_storage_hint=local_storage_hint,
            )
        )

    for match in CREATE_INDEX_RE.finditer(value):
        table = _strip_identifier(match.group("table"))
        evidence.append(
            SourceEvidence(
                kind="create_index",
                path=path,
                line=line,
                symbol=symbol,
                table=table,
                detail=_strip_identifier(match.group("name")),
                local_storage_hint=local_storage_hint,
            )
        )

    # Only classify query references as local-storage evidence when the source
    # itself has a SQLite signal. This deliberately avoids treating MSSQL query
    # strings as missing local tables.
    if local_storage_hint:
        for match in TABLE_REF_RE.finditer(value):
            table = _strip_identifier(match.group("table"))
            if table.lower() not in SQLITE_INTERNAL_TABLES:
                evidence.append(
                    SourceEvidence(
                        kind="table_reference",
                        path=path,
                        line=line,
                        symbol=symbol,
                        table=table,
                        detail=match.group(0).strip(),
                        local_storage_hint=True,
                    )
                )

    return evidence


def _mark_runtime_invocation(
    evidence: list[SourceEvidence],
    functions: dict[str, FunctionRecord],
) -> None:
    called_by: dict[str, set[str]] = {}
    for caller_symbol, record in functions.items():
        for callee in record.calls:
            if callee in functions:
                called_by.setdefault(callee, set()).add(caller_symbol)

    def ancestors(symbol: str, limit: int = 5) -> list[str]:
        seen: set[str] = set()
        frontier = [symbol]
        output: list[str] = []
        for _ in range(limit):
            next_frontier: list[str] = []
            for item in frontier:
                for parent in sorted(called_by.get(item, set())):
                    if parent in seen:
                        continue
                    seen.add(parent)
                    output.append(parent)
                    next_frontier.append(parent)
            frontier = next_frontier
            if not frontier:
                break
        return output

    for item in evidence:
        if item.kind != "create_table" or not item.symbol:
            continue

        normalized_path = item.path.replace("\\", "/")
        if normalized_path.startswith("scripts/"):
            item.runtime_invoked = False
            item.runtime_reason = "schema creation is located under scripts/"
            continue

        record = functions.get(item.symbol)
        if not record:
            continue

        if record.name == "__init__":
            item.runtime_invoked = True
            item.runtime_reason = "schema creation occurs in repository/class construction"
            continue

        lineage = ancestors(item.symbol)
        init_parent = next(
            (
                parent
                for parent in lineage
                if functions.get(parent) and functions[parent].name == "__init__"
            ),
            None,
        )
        if init_parent:
            item.runtime_invoked = True
            item.runtime_reason = f"invoked from constructor {init_parent}"
            continue

        startup_parent = next(
            (
                parent
                for parent in lineage
                if parent.startswith("app.main.")
                or ".bootstrap." in parent
                or parent.endswith(".bootstrap")
            ),
            None,
        )
        if startup_parent:
            item.runtime_invoked = True
            item.runtime_reason = f"reachable from runtime bootstrap {startup_parent}"
            continue

        item.runtime_reason = "runtime invocation not proven by static analysis"


def scan_source(root: Path) -> SourceScan:
    all_evidence: list[SourceEvidence] = []
    all_functions: dict[str, FunctionRecord] = {}

    py_files = [
        path
        for path in root.rglob("*.py")
        if not any(part in DEFAULT_EXCLUDED_DIRS for part in path.relative_to(root).parts)
    ]

    for path in sorted(py_files):
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        visitor = _ModuleVisitor(_module_name(root, path), relative)
        visitor.visit(tree)
        all_functions.update(visitor.functions)

        module_strings = visitor.module_strings[:]
        for record in visitor.functions.values():
            local_hint = _is_local_storage_module(
                relative,
                module_strings + record.strings,
                visitor.imports,
            )

            for value, line in record.strings:
                all_evidence.extend(
                    _extract_string_evidence(
                        value=value,
                        line=line,
                        path=relative,
                        symbol=record.symbol,
                        local_storage_hint=local_hint,
                    )
                )

            # Detect sqlite3.connect / SQLAlchemy create_engine calls structurally.
            for call in record.calls:
                if call in {"sqlite3.connect", "sqlite3.dbapi2.connect"}:
                    all_evidence.append(
                        SourceEvidence(
                            kind="sqlite_connect",
                            path=relative,
                            line=record.line,
                            symbol=record.symbol,
                            detail=call,
                            local_storage_hint=True,
                        )
                    )

        local_module_hint = _is_local_storage_module(
            relative,
            module_strings,
            visitor.imports,
        )
        for value, line in module_strings:
            all_evidence.extend(
                _extract_string_evidence(
                    value=value,
                    line=line,
                    path=relative,
                    symbol=None,
                    local_storage_hint=local_module_hint,
                )
            )

    # Scan standalone SQL under scripts/ for schema-definition evidence, while
    # explicitly avoiding MSSQL migration directories.
    for path in sorted(root.rglob("*.sql")):
        relative = path.relative_to(root).as_posix()
        parts_lower = {part.lower() for part in path.relative_to(root).parts}
        if "mssql_migrations" in parts_lower or "prod" in parts_lower:
            continue
        if "scripts" not in parts_lower:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for match in CREATE_TABLE_RE.finditer(text):
            table = _strip_identifier(match.group("name"))
            all_evidence.append(
                SourceEvidence(
                    kind="create_table",
                    path=relative,
                    line=text[: match.start()].count("\n") + 1,
                    symbol=None,
                    table=table,
                    detail="standalone SQL schema definition",
                    declared_columns=_split_sql_columns(match.group("body")),
                    local_storage_hint=True,
                    runtime_invoked=False,
                    runtime_reason="standalone script/SQL schema definition",
                )
            )

    _mark_runtime_invocation(all_evidence, all_functions)

    # De-duplicate exact evidence records to reduce noisy repeated SQL strings.
    unique: dict[tuple, SourceEvidence] = {}
    for item in all_evidence:
        key = (
            item.kind,
            item.path,
            item.line,
            item.symbol,
            (item.table or "").lower(),
            item.db_literal,
            item.detail,
        )
        unique[key] = item

    return SourceScan(
        evidence=sorted(
            unique.values(),
            key=lambda item: (
                item.path,
                item.line,
                item.kind,
                item.table or "",
            ),
        )
    )
