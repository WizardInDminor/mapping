# OIP Local Storage Audit V0.1 — First Run Checklist

Run this independently from the backend catalog:

```bash
python -m tools.local_storage_audit scan
```

Then review:

```text
docs/reference/generated/local_storage_audit.md
```

## First OIP calibration targets

Pay particular attention to:

1. `coordination.db`
   - especially `priority_assignments`
   - does the report find a schema creator?
   - does it classify that creator as runtime/bootstrap owned or script-only?

2. `equipment_status_notes.db`
   - the expected healthy pattern is a repository-owned `_ensure_schema()` path.

3. `employee_enrichment.db`
   - identify whether schema ownership is runtime, bootstrap, migration, or manual.

4. Root-level/legacy stores such as `priority_notes.db` and `proficiency_planning.db`
   - look for `ORPHANED`, `UNOWNED`, or `UNKNOWN` findings.

5. `var/test/` stores
   - compare their table shapes and bootstrap ownership with their production counterparts.

## Do not enable CI enforcement yet

V0.1 intentionally exposes uncertainty. Do **not** use `--fail-on-risk` in CI until
we have reviewed the first real OIP report and calibrated any source patterns the
static scanner does not yet recognize.

## If the first report is large

The most useful excerpt to share for calibration is:

- the Audit Summary;
- the SQLite Stores table;
- the full section for `coordination.db`;
- any `MANUAL`, `UNOWNED`, `DRIFT`, or `MISSING` findings.

You do not need to share database contents.
