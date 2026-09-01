# OIP Backend Catalog — V0.1

This package statically inventories the OIP Python backend without importing or executing the application.

## What V0.1 extracts

- Python functions, classes, and methods
- Architectural layer and role inferred from the OIP directory structure
- Function signatures, annotations, defaults, and docstrings
- Intra-backend calls with basic import and constructed-service resolution
- `called_by` relationships when a call resolves to a scanned symbol
- FastAPI `APIRouter` routes, local router prefixes, response models, and simple `include_router(...)` mounts
- SQL template filenames loaded through `_load_sql_template(...)` / `load_sql_template(...)`
- Candidate SQL file locations by filename
- Static table names found in inline SQL and referenced SQL templates
- Source file and line number

It emits a machine-readable JSON inventory and a MkDocs-friendly Markdown view.

## Installation

Copy the `tools/` directory into the backend repository root:

```text
backend/
├── app/
├── prod/
├── tools/
│   └── backend_catalog/
└── ...
```

No new Python dependency is required; V0.1 uses only the standard library.

## Build

From the backend repository root:

```bash
python -m tools.backend_catalog build
```

Default output:

```text
docs/reference/generated/backend_catalog.json
docs/reference/generated/backend_catalog.md
```

If your MkDocs content lives somewhere else:

```bash
python -m tools.backend_catalog build --output-dir path/to/generated
```

## Check for stale generated docs

```bash
python -m tools.backend_catalog check
```

Exit code is `1` when either generated file is missing or differs from the current source tree. This is suitable for a later CI check.

## Deliberate V0.1 limitations

This scanner is intentionally conservative. It does **not** import modules or execute application code. Dynamic imports, dynamically constructed route paths, runtime-selected SQL, and indirect dependency injection may therefore remain unresolved.

The generated catalog is an implementation inventory, not yet the semantic **Capability Catalog**. The next layer should join this generated inventory to a small human-maintained `capabilities.yaml` whose entries answer questions such as “What operational shift owns a datetime?” and point at canonical generated symbols.

## Recommended next steps after the first OIP run

1. Run `build` and inspect unresolved/incorrect routes and calls.
2. Patch only patterns that actually occur in OIP.
3. Add `capabilities.yaml` and validate canonical entrypoints against the generated symbol IDs.
4. Add a generated capability-first MkDocs page.
5. Add `check` to CI once the scanner is trustworthy.
