from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .capabilities import load_capabilities, validate_capabilities
from .catalog_io import load_catalog_json
from .render import render_json, render_markdown, write_outputs
from .render_capabilities import render_capability_markdown
from .scanner import scan_project


def _common_args(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--root", type=Path, default=Path.cwd(), help="Backend project root (default: current directory).")
    cmd.add_argument("--scan-root", type=Path, default=None, help="Python scan root (default: <root>/app).")
    cmd.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/reference/generated"),
        help="Output directory relative to project root unless absolute.",
    )
    cmd.add_argument(
        "--capabilities",
        type=Path,
        default=Path("docs/reference/capabilities.yaml"),
        help="Capability registry relative to project root unless absolute.",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.backend_catalog",
        description="Generate OIP backend inventory and human-oriented capability documentation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("build", "build-inventory", "check"):
        cmd = sub.add_parser(name)
        _common_args(cmd)

    cap = sub.add_parser("build-capabilities")
    _common_args(cap)
    cap.add_argument(
        "--catalog-json",
        type=Path,
        default=None,
        help="Optional existing backend_catalog.json to render from instead of rescanning source.",
    )
    return parser


def _resolve(root: Path, value: Path | None) -> Path | None:
    if value is None:
        return None
    return value if value.is_absolute() else root / value


def _paths(args) -> tuple[Path, Path | None, Path, Path]:
    root = args.root.resolve()
    scan_root = _resolve(root, args.scan_root)
    output_dir = _resolve(root, args.output_dir)
    capabilities_path = _resolve(root, args.capabilities)
    assert output_dir is not None
    assert capabilities_path is not None
    return root, scan_root, output_dir, capabilities_path


def _render_capabilities_or_fail(catalog, capabilities_path: Path) -> tuple[str | None, int]:
    try:
        registry = load_capabilities(capabilities_path)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Capability registry error: {exc}")
        return None, 2

    validation = validate_capabilities(registry, catalog)
    for warning in validation.warnings:
        print(f"Capability warning: {warning}")
    if validation.errors:
        print("Capability validation failed:")
        for error in validation.errors:
            print(f"  - {error}")
        return None, 2

    return render_capability_markdown(registry, catalog), 0


def main() -> int:
    args = _parser().parse_args()
    root, scan_root, output_dir, capabilities_path = _paths(args)

    if args.command == "build-capabilities" and args.catalog_json is not None:
        catalog_json = _resolve(root, args.catalog_json)
        assert catalog_json is not None
        catalog = load_catalog_json(catalog_json)
    else:
        catalog = scan_project(root, scan_root)

    if args.command == "build-inventory":
        json_path, md_path = write_outputs(catalog, output_dir)
        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")
        print(f"Symbols: {len(catalog.symbols)} | Routes: {len(catalog.routes)} | Warnings: {len(catalog.warnings)}")
        return 0

    if args.command == "build-capabilities":
        content, rc = _render_capabilities_or_fail(catalog, capabilities_path)
        if rc:
            return rc
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "backend_capabilities.md"
        path.write_text(content or "", encoding="utf-8")
        print(f"Wrote {path}")
        return 0

    capability_content, rc = _render_capabilities_or_fail(catalog, capabilities_path)
    if rc:
        return rc

    if args.command == "build":
        json_path, md_path = write_outputs(catalog, output_dir)
        capability_path = output_dir / "backend_capabilities.md"
        capability_path.write_text(capability_content or "", encoding="utf-8")
        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")
        print(f"Wrote {capability_path}")
        print(f"Symbols: {len(catalog.symbols)} | Routes: {len(catalog.routes)} | Warnings: {len(catalog.warnings)}")
        return 0

    expected = {
        output_dir / "backend_catalog.json": render_json(catalog),
        output_dir / "backend_inventory.md": render_markdown(catalog),
        output_dir / "backend_capabilities.md": capability_content or "",
    }
    stale: list[Path] = []
    for path, content in expected.items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            stale.append(path)

    if stale:
        print("Backend documentation is stale or missing:")
        for path in stale:
            print(f"  - {path}")
        print("Run: python -m tools.backend_catalog build")
        return 1

    print("Backend inventory and capability catalog are current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
