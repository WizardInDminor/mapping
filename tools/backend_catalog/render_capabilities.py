from __future__ import annotations

from collections import defaultdict

from .capabilities import CapabilityRecord, CapabilityRegistry
from .models import CatalogRecord, RouteRecord, SymbolRecord
from .render import _sig


IMPLEMENTATION_ROLES = {"route", "service", "workflow", "context_builder", "repository"}


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


def _direct_internal_calls(symbol: SymbolRecord, symbols: dict[str, SymbolRecord]) -> list[SymbolRecord]:
    found: list[SymbolRecord] = []
    seen: set[str] = set()
    for call in symbol.calls:
        if not call.resolved or call.resolved in seen:
            continue
        target = symbols.get(call.resolved)
        if (
            target is None
            or target.role not in IMPLEMENTATION_ROLES
            or target.kind not in {"function", "method"}
        ):
            continue
        seen.add(call.resolved)
        found.append(target)
    return found


def _implementation_tree(
    entrypoint: SymbolRecord,
    symbols: dict[str, SymbolRecord],
    *,
    max_depth: int = 3,
) -> list[tuple[int, SymbolRecord]]:
    rows: list[tuple[int, SymbolRecord]] = []
    visited: set[str] = {entrypoint.symbol_id}

    def walk(symbol: SymbolRecord, depth: int) -> None:
        if depth > max_depth:
            return
        for target in _direct_internal_calls(symbol, symbols):
            if target.symbol_id in visited:
                continue
            visited.add(target.symbol_id)
            rows.append((depth, target))
            if target.role != "repository":
                walk(target, depth + 1)

    walk(entrypoint, 1)
    return rows


def _route_text(route: RouteRecord) -> str:
    paths = route.full_paths or [route.local_path]
    return ", ".join(f"`{route.method} {path}`" for path in paths)


def _high_confidence_tables(symbols: list[SymbolRecord]) -> list[str]:
    tables: set[str] = set()
    for symbol in symbols:
        for table in symbol.tables:
            # Qualified objects are much less likely to be CTE names or prose false positives.
            if "." in table and " " not in table:
                tables.add(table)
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
        # One reverse hop covers the common service <- route shape without pretending
        # we can prove every possible transitive API exposure.
        for caller_id in symbol.called_by:
            for route in routes_by_handler.get(caller_id, []):
                add_route(route)

    return sorted(routes, key=lambda route: ((route.full_paths or [route.local_path])[0], route.method))


def render_capability_markdown(registry: CapabilityRegistry, catalog: CatalogRecord) -> str:
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

    for capability in sorted(registry.capabilities, key=lambda item: (item.category.lower(), item.name.lower())):
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

    for category, capabilities in sorted(grouped.items(), key=lambda item: item[0].lower()):
        out += ["", f"## {category}", ""]
        for capability in sorted(capabilities, key=lambda item: item.name.lower()):
            out += [f"### {capability.name}", "", f"<a id=\"{capability.id.replace('.', '-')}\"></a>", ""]
            out.append(f"**Question:** {capability.question}")
            out.append("")
            out.append(f"**Status:** `{capability.status}`")
            out.append("")
            if capability.summary:
                out += [capability.summary, ""]

            routes = _capability_routes(capability, symbols, routes_by_handler)
            if routes:
                out += ["**API surface**", ""]
                for route in routes:
                    response = f" → `{route.response_model}`" if route.response_model else ""
                    out.append(f"- {_route_text(route)}{response}")
                out.append("")

            entrypoint_symbols = [symbols[item] for item in capability.entrypoints if item in symbols]
            if entrypoint_symbols:
                out += ["**Canonical entrypoints**", ""]
                for symbol in entrypoint_symbols:
                    out.append(
                        f"- `{symbol.symbol_id}` — `{_sig(symbol)}` "
                        f"({symbol.layer or 'other'} / {symbol.role or 'other'}; "
                        f"`{symbol.source_file}:{symbol.source_line}`)"
                    )
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
                out += ["**Resolved implementation**", ""]
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
                by_id = {item.id: item for item in registry.capabilities}
                for related_id in capability.related:
                    related = by_id.get(related_id)
                    related_names.append(
                        f"[{related.name}](#{related.id.replace('.', '-')})" if related else f"`{related_id}`"
                    )
                out += ["**Related capabilities:** " + ", ".join(related_names), ""]

    return "\n".join(out).rstrip() + "\n"
