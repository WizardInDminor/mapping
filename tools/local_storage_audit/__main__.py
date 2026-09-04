from __future__ import annotations

import argparse
from pathlib import Path

from .audit import run_audit
from .render import write_json, write_markdown


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.local_storage_audit",
        description=(
            "Explicit, read-only audit of local SQLite storage, schema ownership, "
            "and bootstrap reproducibility."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser(
        "scan",
        help="Inspect local SQLite files and reconcile them with source/bootstrap evidence.",
    )
    scan.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Repository/backend root to scan. Defaults to the current directory.",
    )
    scan.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/reference/generated"),
        help="Output directory for generated audit files.",
    )
    scan.add_argument(
        "--no-row-counts",
        action="store_true",
        help="Skip SELECT COUNT(*) aggregation for every local table.",
    )
    scan.add_argument(
        "--fail-on-risk",
        action="store_true",
        help=(
            "Return exit code 2 when DRIFT, MISSING, UNOWNED, or MANUAL findings exist. "
            "Disabled by default."
        ),
    )

    return parser


def main() -> int:
    args = _build_parser().parse_args()

    if args.command == "scan":
        report = run_audit(
            args.root,
            include_row_counts=not args.no_row_counts,
        )

        output_dir = args.output_dir
        if not output_dir.is_absolute():
            output_dir = args.root / output_dir

        json_path = output_dir / "local_storage_audit.json"
        md_path = output_dir / "local_storage_audit.md"

        write_json(report, json_path)
        write_markdown(report, md_path)

        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")
        print(
            f"Discovered {len(report.databases)} SQLite file(s), "
            f"audited {len(report.table_audits)} observed table(s), "
            f"and found {len(report.missing_tables)} expected-but-missing table(s)."
        )

        if args.fail_on_risk:
            risky = {"DRIFT", "MISSING", "UNOWNED", "MANUAL"}
            statuses = {item.status for item in report.table_audits}
            statuses.update(item.status for item in report.missing_tables)
            if statuses & risky:
                return 2

        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
