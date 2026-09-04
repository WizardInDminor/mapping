from __future__ import annotations

import ast
import re
from collections import defaultdict

from .capabilities import CapabilityRecord, CapabilityRegistry
from .models import CatalogRecord, ParameterRecord, RouteRecord, SymbolRecord

IMPLEMENTATION_ROLES = {"service", "workflow", "context_builder", "repository"}
IMPLEMENTATION_PRIORITY = {
    "workflow": 0,
    "context_builder": 1,
    "service": 2,
    "repository": 3,
}
QUALIFIED_DB_OBJECT_RE = re.compile(
    r"^[A-Za-z_#][A-Za-z0-9_$#]*(?:\.[A-Za-z_#][A-Za-z0-9_$#]*){1,3}$"
)


def _symbol_map(catalog: CatalogRecord) -> dict[str, SymbolRecord]:
    return {symbol.symbol_id: symbol for symbol in catalog.symbols}


def _routes_by_handler(catalog: CatalogRecord) -> dict[str, list[RouteRecord]]:
    result: dict[str, list[RouteRecord]] = defaultdict(list)
    for route in catalog.routes:
        result[route.handler_symbol].append(route)
    return result


def _short(symbol_id: str) -> str:
    parts = symbol_id.split(".")
    return ".".join(parts[-2:]) if len(parts) > 1 else symbol_id


def _direct_internal_calls(
    symbol: SymbolRecord,
    symbols: dict[str, SymbolRecord],
) -> list[SymbolRecord]:
    found: list[SymbolRecord] = []
    seen: set[str] = set()
    for call in symbol.calls:
        if not call.resolved or call.resolved in seen:
            continue
        target = symbols.get(call.resolved)
        if target is None or target.kind not in {"function", "method"}:
            continue
        seen.add(call.resolved)
        found.append(target)
    return found


def _implementation_tree(
    entrypoint: SymbolRecord,
    symbols: dict[str, SymbolRecord],
    *,
    max_depth: int = 4,
) -> list[tuple[int, SymbolRecord]]:
    """Return a bounded static implementation trace.

    Route-local helpers are traversed but omitted from the rendered trace. This
    keeps the human guide focused on reusable backend implementation boundaries
    while still allowing a helper to lead to a service/repository dependency.
    """
    rows: list[tuple[int, SymbolRecord]] = []
    visited: set[str] = {entrypoint.symbol_id}

    def walk(symbol: SymbolRecord, depth: int) -> None:
        if depth > max_depth:
            return
        for target in _direct_internal_calls(symbol, symbols):
            if target.symbol_id in visited:
                continue
            visited.add(target.symbol_id)
            if target.role in IMPLEMENTATION_ROLES:
                rows.append((depth, target))
            if target.role != "repository":
                walk(target, depth + 1)

    walk(entrypoint, 1)
    return rows


def _canonical_implementations(
    entrypoint_symbols: list[SymbolRecord],
    symbols: dict[str, SymbolRecord],
) -> list[SymbolRecord]:
    """Derive the first reusable backend implementation boundary per anchor."""
    result: list[SymbolRecord] = []
    seen: set[str] = set()

    for entrypoint in entrypoint_symbols:
        if entrypoint.role in IMPLEMENTATION_ROLES:
            candidates = [entrypoint]
        else:
            candidates = [
                target
                for target in _direct_internal_calls(entrypoint, symbols)
                if target.role in IMPLEMENTATION_ROLES
            ]
            candidates.sort(
                key=lambda item: (
                    IMPLEMENTATION_PRIORITY.get(item.role or "", 99),
                    item.symbol_id,
                )
            )

        if not candidates:
            # Fall back to the first meaningful node reachable through a route helper.
            trace = _implementation_tree(entrypoint, symbols, max_depth=2)
            candidates = [symbol for _, symbol in trace]
            candidates.sort(
                key=lambda item: (
                    IMPLEMENTATION_PRIORITY.get(item.role or "", 99),
                    item.symbol_id,
                )
            )

        if candidates:
            candidate = candidates[0]
            if candidate.symbol_id not in seen:
                seen.add(candidate.symbol_id)
                result.append(candidate)

    return result


def _route_text(route: RouteRecord) -> str:
    paths = route.full_paths or [route.local_path]
    return ", ".join(f"`{route.method} {path}`" for path in paths)


def _is_high_confidence_database_object(value: str) -> bool:
    """Conservatively identify qualified SQL objects for human-facing lineage."""
    value = value.strip().replace("[", "").replace("]", "")
    if value.startswith(".") or value.endswith(".") or " " in value:
        return False
    return bool(QUALIFIED_DB_OBJECT_RE.fullmatch(value))


def _high_confidence_tables(symbols: list[SymbolRecord]) -> list[str]:
    tables: set[str] = set()
    for symbol in symbols:
        for table in symbol.tables:
            cleaned = table.strip().replace("[", "").replace("]", "")
            if _is_high_confidence_database_object(cleaned):
                tables.add(cleaned)
    return sorted(tables)


def _sql_templates(symbols: list[SymbolRecord]) -> list[str]:
    return sorted({name for symbol in symbols for name in symbol.sql_files})


def _capability_routes(
    capability: CapabilityRecord,
    symbols: dict[str, SymbolRecord],
    routes_by_handler: dict[str, list[RouteRecord]],
) -> list[RouteRecord]:
    routes: list[RouteRecord] = []
    seen: set[tuple[str, str, str]] = set()

    def add_route(route: RouteRecord) -> None:
        key = (route.method, route.handler_symbol, "|".join(route.full_paths))
        if key not in seen:
            seen.add(key)
            routes.append(route)

    for entrypoint_id in capability.entrypoints:
        for route in routes_by_handler.get(entrypoint_id, []):
            add_route(route)

        symbol = symbols.get(entrypoint_id)
        if symbol is None:
            continue
        # One reverse hop covers the common service <- route shape without
        # claiming that every transitive caller is a supported public API.
        for caller_id in symbol.called_by:
            for route in routes_by_handler.get(caller_id, []):
                add_route(route)

    return sorted(
        routes,
        key=lambda route: ((route.full_paths or [route.local_path])[0], route.method),
    )


def _clean_annotation(annotation: str | None) -> str:
    if not annotation:
        return "untyped"
    try:
        expr = ast.parse(annotation, mode="eval").body
    except SyntaxError:
        return annotation

    if isinstance(expr, ast.Subscript):
        base = expr.value
        base_name = base.id if isinstance(base, ast.Name) else None
        if base_name == "Annotated":
            slice_node = expr.slice
            if isinstance(slice_node, ast.Tuple) and slice_node.elts:
                try:
                    return ast.unparse(slice_node.elts[0])
                except Exception:
                    return annotation
    return annotation


def _call_default(expression: str | None, function_name: str) -> tuple[bool, str | None]:
    """Return (is_wrapper, effective_default) for Query()/Path()-style defaults."""
    if not expression:
        return False, None
    try:
        node = ast.parse(expression, mode="eval").body
    except SyntaxError:
        return False, expression
    if not isinstance(node, ast.Call):
        return False, expression

    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if name != function_name:
        return False, expression

    default_node = None
    if node.args:
        default_node = node.args[0]
    for kw in node.keywords:
        if kw.arg == "default":
            default_node = kw.value
            break

    if default_node is None:
        return True, None
    if isinstance(default_node, ast.Constant) and default_node.value is Ellipsis:
        return True, "required"
    if isinstance(default_node, ast.Constant) and default_node.value is None:
        return True, "None"
    try:
        return True, ast.unparse(default_node)
    except Exception:
        return True, None


def _is_dependency_parameter(parameter: ParameterRecord) -> bool:
    default = parameter.default or ""
    return "Depends(" in default or default.startswith("Security(")


def _parameter_contract(parameter: ParameterRecord) -> str | None:
    if parameter.name in {"self", "cls"} or _is_dependency_parameter(parameter):
        return None

    annotation = _clean_annotation(parameter.annotation)
    default = parameter.default

    for wrapper in ("Query", "Path", "Header", "Cookie", "Body"):
        wrapped, effective = _call_default(default, wrapper)
        if wrapped:
            if effective in {None, "required"}:
                suffix = "required"
            elif effective == "None":
                suffix = "optional"
            else:
                suffix = f"default `{effective}`"
            return f"`{parameter.name}`: `{annotation}` ({suffix})"

    if default is None:
        return f"`{parameter.name}`: `{annotation}` (required)"
    if default == "None":
        return f"`{parameter.name}`: `{annotation}` (optional)"
    return f"`{parameter.name}`: `{annotation}` (default `{default}`)"


def _capability_contract(
    capability: CapabilityRecord,
    symbols: dict[str, SymbolRecord],
    routes: list[RouteRecord],
) -> tuple[list[str], list[str]]:
    route_handlers = [symbols[route.handler_symbol] for route in routes if route.handler_symbol in symbols]
    sources = route_handlers or [symbols[item] for item in capability.entrypoints if item in symbols]

    inputs: list[str] = []
    seen_inputs: set[str] = set()
    for symbol in sources:
        for parameter in symbol.parameters:
            rendered = _parameter_contract(parameter)
            if rendered and rendered not in seen_inputs:
                seen_inputs.add(rendered)
                inputs.append(rendered)

    returns: list[str] = []
    seen_returns: set[str] = set()
    if routes:
        for route in routes:
            if route.response_model and route.response_model not in seen_returns:
                seen_returns.add(route.response_model)
                returns.append(route.response_model)
    else:
        for symbol in sources:
            if symbol.return_annotation and symbol.return_annotation not in seen_returns:
                seen_returns.add(symbol.return_annotation)
                returns.append(symbol.return_annotation)

    return inputs, returns


def _implementation_contract(symbol: SymbolRecord) -> str:
    return_type = f" → `{symbol.return_annotation}`" if symbol.return_annotation else ""
    return (
        f"`{symbol.symbol_id}`{return_type} "
        f"({symbol.layer or 'other'} / {symbol.role or 'other'}; "
        f"`{symbol.source_file}:{symbol.source_line}`)"
    )


def render_capability_markdown(
    registry: CapabilityRegistry,
    catalog: CatalogRecord,
) -> str:
    symbols = _symbol_map(catalog)
    routes_by_handler = _routes_by_handler(catalog)

    out: list[str] = [
        "# Backend Capability Catalog",
        "",
        "> Human-oriented guide generated from `capabilities.yaml` and the backend source inventory. Do not edit this generated file directly.",
        "",
        f"- Capability schema: `{registry.capability_version}`",
        f"- Backend catalog: `{catalog.catalog_version}`",
        f"- Source fingerprint: `{catalog.source_fingerprint[:16]}…`",
        f"- Registered capabilities: **{len(registry.capabilities)}**",
        "",
        "## Quick Capability Index",
        "",
        "| Question | Capability | Category | API |",
        "|---|---|---|---|",
    ]

    for capability in sorted(
        registry.capabilities,
        key=lambda item: (item.category.lower(), item.name.lower()),
    ):
        routes = _capability_routes(capability, symbols, routes_by_handler)
        api = "<br>".join(_route_text(route) for route in routes) if routes else "—"
        question = capability.question.replace("|", "\\|")
        out.append(
            f"| {question} | [{capability.name}](#{capability.id.replace('.', '-')}) | "
            f"{capability.category} | {api} |"
        )

    grouped: dict[str, list[CapabilityRecord]] = defaultdict(list)
    for capability in registry.capabilities:
        grouped[capability.category].append(capability)

    by_id = {item.id: item for item in registry.capabilities}

    for category, capabilities in sorted(grouped.items(), key=lambda item: item[0].lower()):
        out += ["", f"## {category}", ""]
        for capability in sorted(capabilities, key=lambda item: item.name.lower()):
            out += [
                f"### {capability.name}",
                "",
                f'<a id="{capability.id.replace(".", "-")}"></a>',
                "",
                f"**Question:** {capability.question}",
                "",
                f"**Status:** `{capability.status}`",
                "",
            ]
            if capability.summary:
                out += [capability.summary, ""]

            routes = _capability_routes(capability, symbols, routes_by_handler)
            if routes:
                out += ["**Public API**", ""]
                for route in routes:
                    response = f" → `{route.response_model}`" if route.response_model else ""
                    out.append(f"- {_route_text(route)}{response}")
                out.append("")

            inputs, returns = _capability_contract(capability, symbols, routes)
            if inputs or returns:
                out += ["**Capability contract**", ""]
                if inputs:
                    out.append("Inputs:")
                    out.extend(f"- {item}" for item in inputs)
                else:
                    out.append("Inputs: none")
                if returns:
                    out.append("Returns: " + ", ".join(f"`{item}`" for item in returns))
                out.append("")

            entrypoint_symbols = [symbols[item] for item in capability.entrypoints if item in symbols]
            canonical = _canonical_implementations(entrypoint_symbols, symbols)
            if canonical:
                out += ["**Canonical backend implementation**", ""]
                out.extend(f"- {_implementation_contract(symbol)}" for symbol in canonical)
                out.append("")

            implementation_nodes: list[SymbolRecord] = []
            implementation_rows: list[tuple[int, SymbolRecord]] = []
            implementation_seen: set[str] = set()
            for entrypoint in entrypoint_symbols:
                for depth, symbol in _implementation_tree(entrypoint, symbols):
                    if symbol.symbol_id in implementation_seen:
                        continue
                    implementation_seen.add(symbol.symbol_id)
                    implementation_rows.append((depth, symbol))
                    implementation_nodes.append(symbol)

            if implementation_rows:
                out += ["**Implementation trace**", ""]
                for depth, symbol in implementation_rows:
                    indent = "  " * max(depth - 1, 0)
                    out.append(
                        f"{indent}- `{_short(symbol.symbol_id)}` "
                        f"({symbol.layer or 'other'} / {symbol.role or 'other'})"
                    )
                out.append("")

            lineage_symbols = entrypoint_symbols + implementation_nodes
            sql_files = _sql_templates(lineage_symbols)
            tables = _high_confidence_tables(lineage_symbols)
            if sql_files or tables:
                out += ["**Data lineage (high-confidence)**", ""]
                if sql_files:
                    out.append("- SQL templates: " + ", ".join(f"`{name}`" for name in sql_files))
                if tables:
                    out.append("- Qualified database objects: " + ", ".join(f"`{name}`" for name in tables))
                out.append("")

            if capability.use_when:
                out += ["**Use when**", ""]
                out.extend(f"- {item}" for item in capability.use_when)
                out.append("")

            if capability.guidance:
                out += ["**Development guidance**", "", capability.guidance, ""]

            if capability.related:
                related_names = []
                for related_id in capability.related:
                    related = by_id.get(related_id)
                    related_names.append(
                        f"[{related.name}](#{related.id.replace('.', '-')})"
                        if related
                        else f"`{related_id}`"
                    )
                out += ["**Related capabilities:** " + ", ".join(related_names), ""]

    return "\n".join(out).rstrip() + "\n"
