from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import CatalogRecord, SymbolRecord

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only when dependency is absent
    yaml = None


CAPABILITY_SCHEMA_VERSION = "0.2.0"
VALID_STATUSES = {"canonical", "experimental", "legacy", "planned"}


@dataclass(slots=True)
class CapabilityRecord:
    id: str
    name: str
    question: str
    category: str
    status: str = "canonical"
    summary: str | None = None
    entrypoints: list[str] = field(default_factory=list)
    use_when: list[str] = field(default_factory=list)
    guidance: str | None = None
    related: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CapabilityRegistry:
    capability_version: str
    capabilities: list[CapabilityRecord]


@dataclass(slots=True)
class CapabilityValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _as_string_list(value: Any, field_name: str, capability_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Capability {capability_id!r}: {field_name} must be a list of strings")
    return value


def load_capabilities(path: Path) -> CapabilityRegistry:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to read capabilities.yaml. Install it with: pip install PyYAML"
        )
    if not path.exists():
        raise FileNotFoundError(f"Capability registry does not exist: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Capability registry root must be a mapping")

    version = str(data.get("capability_version") or CAPABILITY_SCHEMA_VERSION)
    raw_capabilities = data.get("capabilities") or []
    if not isinstance(raw_capabilities, list):
        raise ValueError("'capabilities' must be a list")

    capabilities: list[CapabilityRecord] = []
    for index, raw in enumerate(raw_capabilities):
        if not isinstance(raw, dict):
            raise ValueError(f"Capability entry #{index + 1} must be a mapping")
        capability_id = str(raw.get("id") or "").strip()
        if not capability_id:
            raise ValueError(f"Capability entry #{index + 1} is missing 'id'")
        name = str(raw.get("name") or "").strip()
        question = str(raw.get("question") or "").strip()
        category = str(raw.get("category") or "").strip()
        if not name or not question or not category:
            raise ValueError(
                f"Capability {capability_id!r} requires non-empty name, question, and category"
            )
        capabilities.append(
            CapabilityRecord(
                id=capability_id,
                name=name,
                question=question,
                category=category,
                status=str(raw.get("status") or "canonical").strip(),
                summary=(str(raw["summary"]).strip() if raw.get("summary") else None),
                entrypoints=_as_string_list(raw.get("entrypoints"), "entrypoints", capability_id),
                use_when=_as_string_list(raw.get("use_when"), "use_when", capability_id),
                guidance=(str(raw["guidance"]).strip() if raw.get("guidance") else None),
                related=_as_string_list(raw.get("related"), "related", capability_id),
                tags=_as_string_list(raw.get("tags"), "tags", capability_id),
            )
        )

    return CapabilityRegistry(capability_version=version, capabilities=capabilities)


def validate_capabilities(registry: CapabilityRegistry, catalog: CatalogRecord) -> CapabilityValidationResult:
    result = CapabilityValidationResult()
    symbols: dict[str, SymbolRecord] = {symbol.symbol_id: symbol for symbol in catalog.symbols}
    capability_ids = {capability.id for capability in registry.capabilities}

    seen: set[str] = set()
    for capability in registry.capabilities:
        if capability.id in seen:
            result.errors.append(f"Duplicate capability id: {capability.id}")
        seen.add(capability.id)

        if capability.status not in VALID_STATUSES:
            result.errors.append(
                f"{capability.id}: invalid status {capability.status!r}; "
                f"expected one of {sorted(VALID_STATUSES)}"
            )

        if not capability.entrypoints and capability.status != "planned":
            result.errors.append(f"{capability.id}: non-planned capability has no entrypoints")

        for entrypoint in capability.entrypoints:
            symbol = symbols.get(entrypoint)
            if symbol is None:
                result.errors.append(f"{capability.id}: entrypoint does not exist: {entrypoint}")
                continue
            if not symbol.public:
                result.warnings.append(f"{capability.id}: entrypoint is private: {entrypoint}")

        for related in capability.related:
            if related not in capability_ids:
                result.errors.append(f"{capability.id}: related capability does not exist: {related}")

    return result
