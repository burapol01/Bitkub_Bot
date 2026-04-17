from __future__ import annotations

import argparse

from services.backup_service import restore_runtime_backup


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restore Bitkub Bot runtime files from a backup bundle.",
    )
    parser.add_argument(
        "--bundle",
        required=True,
        help="Path to the backup zip bundle created by scripts/backup_runtime.py.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow the restore helper to replace existing runtime files.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = restore_runtime_backup(
        bundle_path_value=args.bundle,
        overwrite=bool(args.overwrite),
    )

    print(f"Bundle: {summary.get('bundle_path', 'n/a')}")
    print(f"Success: {summary.get('success', False)}")
    if summary.get("restored_assets"):
        print("Restored assets:")
        for asset in summary["restored_assets"]:
            print(f"- {asset.get('kind')} -> {asset.get('restore_path')}")
    if summary.get("warnings"):
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"- {warning}")
    if summary.get("errors"):
        print("Errors:")
        for error in summary["errors"]:
            print(f"- {error}")

    return 0 if summary.get("success", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
