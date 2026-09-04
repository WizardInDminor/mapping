# OIP Local Storage Audit V0.1

A companion tool to the Backend Capability Catalog for auditing OIP's **local SQLite persistence**.

This tool answers a different question from `tools.backend_catalog`:

> **What local persistent state exists, who consumes it, how is its schema created, and can a clean deployment reproduce it?**

## Safety boundary

`local_storage_audit` is deliberately **not** part of `python -m tools.backend_catalog build`.

It only runs when explicitly invoked:

```bash
python -m tools.local_storage_audit scan
```

The audit:

- discovers `.db`, `.sqlite`, and `.sqlite3` files below the selected root;
- verifies the SQLite file header before opening a file;
- opens SQLite with URI `mode=ro`;
- sets `PRAGMA query_only=ON`;
- reads `sqlite_master` and SQLite PRAGMA metadata;
- optionally performs `SELECT COUNT(*)` aggregates;
- never inserts, updates, deletes, migrates, bootstraps, or creates schema;
- never imports or executes the OIP FastAPI application;
- statically scans Python/SQL source for SQLite paths, consumers, `CREATE TABLE`,
  bootstrap methods, and standalone initialization scripts.

It does **not** connect to MSSQL.

## Installation

Copy this package into the backend repository:

```text
backend/
├── tools/
│   ├── __init__.py
│   ├── backend_catalog/
│   │   └── ...
│   └── local_storage_audit/
│       ├── __init__.py
│       ├── __main__.py
│       ├── audit.py
│       ├── discovery.py
│       ├── models.py
│       ├── reconcile.py
│       ├── render.py
│       ├── source_scanner.py
│       └── sqlite_inspector.py
└── ...
```

V0.1 uses only the Python standard library. No new dependency is required.

## Run

From the backend root:

```bash
python -m tools.local_storage_audit scan
```

Outputs:

```text
docs/reference/generated/
├── local_storage_audit.json
└── local_storage_audit.md
```

The Markdown file is the primary human audit. The JSON preserves the complete
structured result for later tooling.

### Skip row counts

```bash
python -m tools.local_storage_audit scan --no-row-counts
```

Use this if a local SQLite store becomes large enough that full `COUNT(*)` queries
are undesirable.

### Optional CI-style exit code

```bash
python -m tools.local_storage_audit scan --fail-on-risk
```

This returns exit code `2` when the report contains `DRIFT`, `MISSING`, `UNOWNED`,
or `MANUAL` findings. This is **not recommended for CI in V0.1**; first calibrate
the tool against the real OIP repository.

## Status vocabulary

| Status | Meaning |
|---|---|
| `PORTABLE` | Schema creation exists and a runtime initialization path was proven statically. |
| `MANUAL` | Schema creation exists only in a standalone script/SQL path. |
| `UNOWNED` | A local table is consumed, but no schema creator was found. |
| `MISSING` | Local-storage code/schema expects a table not found in discovered DBs. |
| `ORPHANED` | A local table exists, but no current SQLite consumer was detected. |
| `DRIFT` | Observed schema is missing columns declared by discovered CREATE TABLE evidence. |
| `UNKNOWN` | Creation exists, but runtime ownership/reproducibility could not be proven. |

These are audit findings, not absolute truths. Static analysis should prefer
`UNKNOWN` over claiming a runtime relationship it cannot prove.

## What V0.1 intentionally does not do

- It does not display table row contents.
- It does not profile employee IDs, notes, names, or other values.
- It does not run migrations or repair findings.
- It does not infer MSSQL schema.
- It does not automatically run with the backend capability catalog.
- It does not yet build a full cross-tool link between backend capabilities and
  local-storage ownership.

The first OIP run should be treated as a calibration run. In particular, inspect
Priority Assignments, equipment status notes, employee enrichment, and any root-
level legacy databases to see which initialization patterns V0.1 recognizes.
