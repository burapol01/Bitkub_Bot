from __future__ import annotations

import argparse

from services.backup_service import create_runtime_backup


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a timestamped backup bundle for the Bitkub Bot runtime assets.",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="Override the backup root directory. Relative paths are resolved from the project root.",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=90,
        help="Keep backup bundles newer than this many days.",
    )
    parser.add_argument(
        "--include-env",
        action="store_true",
        help="Include the loaded .env file in the backup bundle when one is available.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = create_runtime_backup(
        backup_dir_value=args.backup_dir,
        backup_retention_days=int(args.retention_days),
        include_env_file=bool(args.include_env),
    )

    print(f"Backup bundle: {summary.get('bundle_path', 'n/a')}")
    print(f"Success: {summary.get('success', False)}")
    print(f"Bundle size: {int(summary.get('bundle_size_bytes', 0) or 0):,} bytes")
    print(
        "Assets: "
        f"{int(summary.get('captured_asset_count', 0) or 0)} captured | "
        f"{int(summary.get('failed_asset_count', 0) or 0)} failed | "
        f"{int(summary.get('skipped_asset_count', 0) or 0)} skipped"
    )
    if summary.get("warnings"):
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"- {warning}")
    if summary.get("errors"):
        print("Errors:")
        for error in summary["errors"]:
            print(f"- {error}")
    if summary.get("pruned_backups"):
        print("Pruned backups:")
        for path in summary["pruned_backups"]:
            print(f"- {path}")

    return 0 if summary.get("success", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
