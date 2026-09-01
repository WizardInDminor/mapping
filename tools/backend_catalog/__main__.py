from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .render import render_json, render_markdown, write_outputs
from .scanner import scan_project


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.backend_catalog",
        description="Generate a static backend inventory for OIP.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("build", "check"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--root", type=Path, default=Path.cwd(), help="Backend project root (default: current directory).")
        cmd.add_argument("--scan-root", type=Path, default=None, help="Python scan root (default: <root>/app).")
        cmd.add_argument(
            "--output-dir",
            type=Path,
            default=Path("docs/reference/generated"),
            help="Output directory relative to project root unless absolute.",
        )
    return parser


def _paths(root: Path, scan_root: Path | None, output_dir: Path) -> tuple[Path, Path, Path]:
    root = root.resolve()
    if scan_root is not None and not scan_root.is_absolute():
        scan_root = root / scan_root
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    return root, scan_root, output_dir


def main() -> int:
    args = _parser().parse_args()
    root, scan_root, output_dir = _paths(args.root, args.scan_root, args.output_dir)
    catalog = scan_project(root, scan_root)

    if args.command == "build":
        json_path, md_path = write_outputs(catalog, output_dir)
        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")
        print(f"Symbols: {len(catalog.symbols)} | Routes: {len(catalog.routes)} | Warnings: {len(catalog.warnings)}")
        return 0

    expected = {
        output_dir / "backend_catalog.json": render_json(catalog),
        output_dir / "backend_catalog.md": render_markdown(catalog),
    }
    stale: list[Path] = []
    for path, content in expected.items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            stale.append(path)

    if stale:
        print("Backend catalog is stale or missing:")
        for path in stale:
            print(f"  - {path}")
        print("Run: python -m tools.backend_catalog build")
        return 1

    print("Backend catalog is current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
