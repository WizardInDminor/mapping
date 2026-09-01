from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .models import (
    CallRecord,
    CatalogRecord,
    ParameterRecord,
    RouteRecord,
    SymbolRecord,
)

CATALOG_VERSION = "0.1.0"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
SQL_TABLE_RE = re.compile(
    r"\b(?:from|join|update|into|delete\s+from)\s+([\[\]\w.#]+)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RouterDef:
    symbol: str
    module: str
    variable: str
    prefix: str = ""


@dataclass(slots=True)
class RouterMount:
    child_symbol: str
    parent_router_symbol: str | None
    prefix: str = ""


@dataclass(slots=True)
class ModuleInfo:
    path: Path
    rel_path: Path
    module: str
    tree: ast.Module
    imports: dict[str, str] = field(default_factory=dict)
    object_bindings: dict[str, str] = field(default_factory=dict)
    routers: dict[str, RouterDef] = field(default_factory=dict)
    mounts: list[RouterMount] = field(default_factory=list)


def _safe_unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _expr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _expr_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return _safe_unparse(node) or "<unknown>"


def _join_url(*parts: str) -> str:
    clean = [p.strip("/") for p in parts if p and p != "/"]
    return "/" + "/".join(clean) if clean else "/"


def _module_name(scan_root: Path, path: Path) -> str:
    rel = path.relative_to(scan_root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _classify_layer_role(rel_path: Path) -> tuple[str | None, str | None]:
    parts = rel_path.parts
    layer = None
    role = None

    if "applications" in parts:
        layer = "application"
    elif "coordination" in parts:
        layer = "coordination"
    elif "domains" in parts:
        layer = "domain"
    elif "core" in parts:
        layer = "core"
    elif "db" in parts:
        layer = "database"
    elif "employee_performance" in parts:
        layer = "application"

    stem = rel_path.stem.lower()
    if "routes" in parts or stem in {"router", "routes"} or stem.endswith("_routes"):
        role = "route"
    elif "services" in parts or stem in {"service", "services"} or stem.endswith("_service") or stem.endswith("_services"):
        role = "service"
    elif "repos" in parts or "repo" in parts or "repository" in stem or stem.endswith("_repo"):
        role = "repository"
    elif "dtos" in parts or "schemas" in parts or stem.endswith("_dto") or stem.endswith("_dtos"):
        role = "dto"
    elif "workflows" in parts:
        role = "workflow"
    elif "context_builders" in parts:
        role = "context_builder"

    return layer, role


def _excluded(path: Path, scan_root: Path) -> bool:
    rel = path.relative_to(scan_root)
    parts = {p.lower() for p in rel.parts}
    name = path.name.lower()
    return (
        "__pycache__" in parts
        or "tests" in parts
        or "test" in parts
        or "_archive" in parts
        or "feature_directory_template" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _iter_source_files(scan_root: Path) -> Iterable[Path]:
    for path in sorted(scan_root.rglob("*.py")):
        if not _excluded(path, scan_root):
            yield path


def _all_fingerprint_files(project_root: Path, scan_root: Path) -> list[Path]:
    files = list(_iter_source_files(scan_root))
    for path in sorted(project_root.rglob("*.sql")):
        parts = {p.lower() for p in path.relative_to(project_root).parts}
        if "__pycache__" not in parts and "tests" not in parts and "testing" not in parts:
            files.append(path)
    return sorted(set(files))


def _fingerprint(project_root: Path, scan_root: Path) -> str:
    digest = hashlib.sha256()
    for path in _all_fingerprint_files(project_root, scan_root):
        rel = path.relative_to(project_root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _collect_imports(tree: ast.Module) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                imports[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                target = f"{module}.{alias.name}" if module else alias.name
                imports[local] = target
    return imports


def _resolve_expr(expr: ast.AST, imports: dict[str, str], bindings: dict[str, str], module: str) -> str:
    raw = _expr_name(expr)
    first, *rest = raw.split(".")
    if first in bindings:
        base = bindings[first]
        return ".".join([base, *rest]) if rest else base
    if first in imports:
        base = imports[first]
        return ".".join([base, *rest]) if rest else base
    if len(raw.split(".")) == 1:
        return f"{module}.{raw}"
    return raw


def _constructor_target(call: ast.Call, imports: dict[str, str], bindings: dict[str, str], module: str) -> str | None:
    target = _resolve_expr(call.func, imports, bindings, module)
    if target:
        return target
    return None


def _collect_module_bindings(module: str, tree: ast.Module, imports: dict[str, str]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if isinstance(value, ast.Call):
                target = _constructor_target(value, imports, bindings, module)
                if target:
                    for lhs in targets:
                        if isinstance(lhs, ast.Name):
                            bindings[lhs.id] = target
    return bindings


def _collect_routers(info: ModuleInfo) -> None:
    for node in info.tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        fn = _resolve_expr(value.func, info.imports, info.object_bindings, info.module)
        if not fn.endswith("APIRouter"):
            continue
        prefix = ""
        for kw in value.keywords:
            if kw.arg == "prefix":
                prefix = _literal_string(kw.value) or ""
        for lhs in targets:
            if isinstance(lhs, ast.Name):
                symbol = f"{info.module}.{lhs.id}"
                info.routers[lhs.id] = RouterDef(symbol=symbol, module=info.module, variable=lhs.id, prefix=prefix)


def _resolve_router_arg(node: ast.AST, info: ModuleInfo) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in info.routers:
            return info.routers[node.id].symbol
        return info.imports.get(node.id)
    if isinstance(node, ast.Attribute):
        return _resolve_expr(node, info.imports, info.object_bindings, info.module)
    return None


def _collect_mounts(info: ModuleInfo) -> None:
    for node in ast.walk(info.tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "include_router" or not node.args:
            continue
        child = _resolve_router_arg(node.args[0], info)
        if not child:
            continue
        prefix = ""
        for kw in node.keywords:
            if kw.arg == "prefix":
                prefix = _literal_string(kw.value) or ""
        parent_symbol = None
        if isinstance(node.func.value, ast.Name) and node.func.value.id in info.routers:
            parent_symbol = info.routers[node.func.value.id].symbol
        info.mounts.append(RouterMount(child_symbol=child, parent_router_symbol=parent_symbol, prefix=prefix))


def _parameter_records(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ParameterRecord]:
    records: list[ParameterRecord] = []
    pos_args = list(node.args.posonlyargs) + list(node.args.args)
    defaults = [None] * (len(pos_args) - len(node.args.defaults)) + list(node.args.defaults)
    posonly_count = len(node.args.posonlyargs)
    for idx, (arg, default) in enumerate(zip(pos_args, defaults)):
        kind = "positional_only" if idx < posonly_count else "positional_or_keyword"
        records.append(ParameterRecord(
            name=arg.arg,
            annotation=_safe_unparse(arg.annotation),
            default=_safe_unparse(default),
            kind=kind,
        ))
    if node.args.vararg:
        records.append(ParameterRecord(node.args.vararg.arg, _safe_unparse(node.args.vararg.annotation), None, "var_positional"))
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        records.append(ParameterRecord(arg.arg, _safe_unparse(arg.annotation), _safe_unparse(default), "keyword_only"))
    if node.args.kwarg:
        records.append(ParameterRecord(node.args.kwarg.arg, _safe_unparse(node.args.kwarg.annotation), None, "var_keyword"))
    return records


def _static_string_parts(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else " " for v in node.values)
    return ""


def _tables_from_text(text: str) -> set[str]:
    return {m.group(1).replace("[", "").replace("]", "") for m in SQL_TABLE_RE.finditer(text)}


class FunctionAnalyzer(ast.NodeVisitor):
    def __init__(self, info: ModuleInfo, node: ast.FunctionDef | ast.AsyncFunctionDef):
        self.info = info
        self.node = node
        self.local_bindings = dict(info.object_bindings)
        self.calls: list[CallRecord] = []
        self.sql_files: set[str] = set()
        self.tables: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call):
            target = _constructor_target(node.value, self.info.imports, self.local_bindings, self.info.module)
            if target:
                for lhs in node.targets:
                    if isinstance(lhs, ast.Name):
                        self.local_bindings[lhs.id] = target
        self.tables.update(_tables_from_text(_static_string_parts(node.value)))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.value, ast.Call):
            target = _constructor_target(node.value, self.info.imports, self.local_bindings, self.info.module)
            if target and isinstance(node.target, ast.Name):
                self.local_bindings[node.target.id] = target
        if node.value:
            self.tables.update(_tables_from_text(_static_string_parts(node.value)))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.tables.update(_tables_from_text(node.value))

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        self.tables.update(_tables_from_text(_static_string_parts(node)))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        raw = _expr_name(node.func)
        resolved = _resolve_expr(node.func, self.info.imports, self.local_bindings, self.info.module)
        self.calls.append(CallRecord(raw=raw, resolved=resolved, line=getattr(node, "lineno", None)))
        if raw.split(".")[-1] in {"_load_sql_template", "load_sql_template"} and node.args:
            filename = _literal_string(node.args[0])
            if filename:
                self.sql_files.add(filename)
        self.generic_visit(node)


def _function_symbol(info: ModuleInfo, node: ast.FunctionDef | ast.AsyncFunctionDef, qualname: str, kind: str) -> SymbolRecord:
    layer, role = _classify_layer_role(info.rel_path)
    analyzer = FunctionAnalyzer(info, node)
    analyzer.visit(node)
    return SymbolRecord(
        symbol_id=f"{info.module}.{qualname}",
        module=info.module,
        name=node.name,
        qualname=qualname,
        kind=kind,
        layer=layer,
        role=role,
        public=not node.name.startswith("_"),
        parameters=_parameter_records(node),
        return_annotation=_safe_unparse(node.returns),
        docstring=ast.get_docstring(node),
        calls=analyzer.calls,
        sql_files=sorted(analyzer.sql_files),
        tables=sorted(analyzer.tables),
        source_file=info.rel_path.as_posix(),
        source_line=node.lineno,
    )


def _collect_symbols(info: ModuleInfo) -> list[SymbolRecord]:
    symbols: list[SymbolRecord] = []
    layer, role = _classify_layer_role(info.rel_path)
    for node in info.tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_function_symbol(info, node, node.name, "function"))
        elif isinstance(node, ast.ClassDef):
            symbols.append(SymbolRecord(
                symbol_id=f"{info.module}.{node.name}",
                module=info.module,
                name=node.name,
                qualname=node.name,
                kind="class",
                layer=layer,
                role=role,
                public=not node.name.startswith("_"),
                docstring=ast.get_docstring(node),
                source_file=info.rel_path.as_posix(),
                source_line=node.lineno,
            ))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(_function_symbol(info, child, f"{node.name}.{child.name}", "method"))
    return symbols


def _decorator_route(decorator: ast.AST, info: ModuleInfo) -> tuple[str, str, str, str | None] | None:
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    method = decorator.func.attr.lower()
    if method not in HTTP_METHODS:
        return None
    router_name = _expr_name(decorator.func.value)
    if router_name not in info.routers:
        return None
    local_path = _literal_string(decorator.args[0]) if decorator.args else ""
    if local_path is None:
        local_path = "<dynamic>"
    response_model = None
    for kw in decorator.keywords:
        if kw.arg == "response_model":
            response_model = _safe_unparse(kw.value)
    router = info.routers[router_name]
    return method.upper(), local_path, router.symbol, response_model


def _collect_raw_routes(info: ModuleInfo) -> list[tuple[RouteRecord, str]]:
    routes: list[tuple[RouteRecord, str]] = []
    for node in info.tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            data = _decorator_route(dec, info)
            if not data:
                continue
            method, local_path, router_symbol, response_model = data
            router = next(r for r in info.routers.values() if r.symbol == router_symbol)
            route = RouteRecord(
                method=method,
                local_path=local_path,
                router_symbol=router_symbol,
                router_prefix=router.prefix,
                full_paths=[],
                response_model=response_model,
                handler_symbol=f"{info.module}.{node.name}",
                source_file=info.rel_path.as_posix(),
                line=node.lineno,
            )
            routes.append((route, router_symbol))
    return routes


def _router_prefixes(router_defs: dict[str, RouterDef], mounts: list[RouterMount]) -> dict[str, list[str]]:
    children_by_parent: dict[str | None, list[RouterMount]] = defaultdict(list)
    for mount in mounts:
        children_by_parent[mount.parent_router_symbol].append(mount)

    roots: dict[str, list[str]] = defaultdict(list)
    # Direct app.include_router(...) calls have parent_router_symbol=None.
    for mount in children_by_parent[None]:
        roots[mount.child_symbol].append(mount.prefix)

    # Routers never mounted still get a local-only path.
    for symbol in router_defs:
        roots.setdefault(symbol, [""])

    changed = True
    passes = 0
    while changed and passes < 50:
        changed = False
        passes += 1
        for mount in mounts:
            if mount.parent_router_symbol is None:
                continue
            parent_prefixes = roots.get(mount.parent_router_symbol, [])
            if not parent_prefixes:
                continue
            child_values = roots.setdefault(mount.child_symbol, [])
            for parent_prefix in parent_prefixes:
                parent_router_prefix = router_defs.get(mount.parent_router_symbol, RouterDef("", "", "", "")).prefix
                combined = _join_url(parent_prefix, parent_router_prefix, mount.prefix)
                if combined not in child_values:
                    child_values.append(combined)
                    changed = True
    return roots


def _resolve_sql_files(project_root: Path, symbols: list[SymbolRecord]) -> None:
    by_name: dict[str, list[Path]] = defaultdict(list)
    for path in project_root.rglob("*.sql"):
        by_name[path.name].append(path)

    for symbol in symbols:
        all_tables = set(symbol.tables)
        for filename in symbol.sql_files:
            candidates = sorted(by_name.get(filename, []))
            rels = [p.relative_to(project_root).as_posix() for p in candidates]
            symbol.sql_file_candidates[filename] = rels
            for candidate in candidates:
                try:
                    all_tables.update(_tables_from_text(candidate.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
        symbol.tables = sorted(all_tables)


def _normalize_call_targets(symbols: list[SymbolRecord]) -> None:
    symbol_ids = {s.symbol_id for s in symbols}
    classes = {s.symbol_id for s in symbols if s.kind == "class"}

    # Resolve constructor-bound instance calls to class methods when possible.
    for symbol in symbols:
        for call in symbol.calls:
            if not call.resolved:
                continue
            if call.resolved in symbol_ids:
                continue
            # If target is Class.method and Class is known, retain it even if method omitted from scan.
            prefix, _, method = call.resolved.rpartition(".")
            if prefix in classes:
                candidate = f"{prefix}.{method}"
                call.resolved = candidate

    callers: dict[str, set[str]] = defaultdict(set)
    for symbol in symbols:
        for call in symbol.calls:
            if call.resolved in symbol_ids:
                callers[call.resolved].add(symbol.symbol_id)
    for symbol in symbols:
        symbol.called_by = sorted(callers.get(symbol.symbol_id, set()))


def scan_project(project_root: Path, scan_root: Path | None = None) -> CatalogRecord:
    project_root = project_root.resolve()
    scan_root = (scan_root or project_root / "app").resolve()
    if not scan_root.exists():
        raise FileNotFoundError(f"Scan root does not exist: {scan_root}")

    modules: list[ModuleInfo] = []
    warnings: list[str] = []

    for path in _iter_source_files(scan_root):
        rel = path.relative_to(project_root)
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as exc:
            warnings.append(f"Could not parse {rel.as_posix()}: {exc}")
            continue
        module = _module_name(project_root, path)
        info = ModuleInfo(path=path, rel_path=rel, module=module, tree=tree)
        info.imports = _collect_imports(tree)
        info.object_bindings = _collect_module_bindings(module, tree, info.imports)
        _collect_routers(info)
        _collect_mounts(info)
        modules.append(info)

    router_defs: dict[str, RouterDef] = {}
    mounts: list[RouterMount] = []
    symbols: list[SymbolRecord] = []
    raw_routes: list[tuple[RouteRecord, str]] = []

    for info in modules:
        router_defs.update({r.symbol: r for r in info.routers.values()})
        mounts.extend(info.mounts)
        symbols.extend(_collect_symbols(info))
        raw_routes.extend(_collect_raw_routes(info))

    router_mount_prefixes = _router_prefixes(router_defs, mounts)
    routes: list[RouteRecord] = []
    for route, router_symbol in raw_routes:
        mount_prefixes = router_mount_prefixes.get(router_symbol) or [""]
        route.full_paths = sorted({
            _join_url(prefix, route.router_prefix, route.local_path)
            for prefix in mount_prefixes
        })
        routes.append(route)

    _normalize_call_targets(symbols)
    _resolve_sql_files(project_root, symbols)

    return CatalogRecord(
        catalog_version=CATALOG_VERSION,
        source_fingerprint=_fingerprint(project_root, scan_root),
        scan_root=scan_root.relative_to(project_root).as_posix(),
        symbols=sorted(symbols, key=lambda s: s.symbol_id),
        routes=sorted(routes, key=lambda r: (r.full_paths[0] if r.full_paths else r.local_path, r.method)),
        warnings=sorted(warnings),
    )
