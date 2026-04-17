from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from config import CONFIG_BASE_PATH, CONFIG_PATH
from services.env_service import get_loaded_env_path
from services.state_service import STATE_FILE_PATH, STATE_PENDING_PATH
from services.db_service import DB_PATH, SQLITE_BUSY_TIMEOUT_MS, SQLITE_TIMEOUT_SECONDS
from utils.time_utils import now_dt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKUP_ROOT = PROJECT_ROOT / "backups"


def _resolve_path(value: str | Path | None, *, default: Path) -> Path:
    candidate = Path(str(value).strip()) if value is not None and str(value).strip() else default
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def resolve_backup_root(backup_dir_value: str | Path | None = None) -> Path:
    return _resolve_path(backup_dir_value, default=DEFAULT_BACKUP_ROOT)


def _collect_existing_sources(*, include_env_file: bool, env_path: Path | None = None) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = [
        {
            "kind": "sqlite_db",
            "source_path": DB_PATH,
            "bundle_path": "sqlite/bitkub.db",
            "restore_path": DB_PATH,
            "required": True,
        },
        {
            "kind": "runtime_state",
            "source_path": STATE_FILE_PATH,
            "bundle_path": "runtime_state/runtime_state.json",
            "restore_path": STATE_FILE_PATH,
            "required": False,
        },
        {
            "kind": "runtime_state_pending",
            "source_path": STATE_PENDING_PATH,
            "bundle_path": "runtime_state/runtime_state.pending.json",
            "restore_path": STATE_PENDING_PATH,
            "required": False,
        },
        {
            "kind": "config",
            "source_path": CONFIG_PATH,
            "bundle_path": "config/config.json",
            "restore_path": CONFIG_PATH,
            "required": True,
        },
    ]

    if CONFIG_BASE_PATH != CONFIG_PATH:
        sources.append(
            {
                "kind": "config_base",
                "source_path": CONFIG_BASE_PATH,
                "bundle_path": "config/config.base.json",
                "restore_path": CONFIG_BASE_PATH,
                "required": True,
            }
        )

    resolved_env_path = env_path or get_loaded_env_path()
    if include_env_file and resolved_env_path is not None:
        sources.append(
            {
                "kind": "env_file",
                "source_path": resolved_env_path,
                "bundle_path": f"env/{resolved_env_path.name}",
                "restore_path": resolved_env_path,
                "required": False,
            }
        )

    return sources


def _snapshot_sqlite_database(source_path: Path, dest_path: Path) -> int:
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_path, timeout=SQLITE_TIMEOUT_SECONDS) as source_conn:
        source_conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        with sqlite3.connect(dest_path, timeout=SQLITE_TIMEOUT_SECONDS) as dest_conn:
            source_conn.backup(dest_conn)

    return dest_path.stat().st_size


def _copy_regular_file(source_path: Path, dest_path: Path) -> int:
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, dest_path)
    return dest_path.stat().st_size


def _build_bundle_name(created_at: datetime) -> str:
    return f"runtime_backup_{created_at.strftime('%Y%m%d_%H%M%S')}.zip"


def _write_manifest(manifest_path: Path, manifest: dict[str, Any]) -> None:
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=True)


def _read_manifest_from_zip(bundle_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(bundle_path, "r") as archive:
        with archive.open("manifest.json") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Backup manifest must be a JSON object")
    return payload


def _zip_bundle_path(bundle_path: Path, source_dir: Path, entries: list[dict[str, Any]]) -> None:
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            file_path = source_dir / str(entry["bundle_path"])
            if not file_path.exists():
                continue
            archive.write(file_path, arcname=str(entry["bundle_path"]))
        archive.write(source_dir / "manifest.json", arcname="manifest.json")


def _bundle_summary_from_manifest(bundle_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    assets = list(manifest.get("assets") or [])
    captured_assets = [asset for asset in assets if str(asset.get("status")) == "captured"]
    failed_assets = [asset for asset in assets if str(asset.get("status")) == "failed"]
    skipped_assets = [asset for asset in assets if str(asset.get("status")) == "skipped"]
    return {
        "bundle_path": str(bundle_path),
        "bundle_name": bundle_path.name,
        "bundle_size_bytes": bundle_path.stat().st_size if bundle_path.exists() else 0,
        "created_at": manifest.get("created_at"),
        "backup_root": manifest.get("backup_root"),
        "asset_count": len(assets),
        "captured_asset_count": len(captured_assets),
        "failed_asset_count": len(failed_assets),
        "skipped_asset_count": len(skipped_assets),
        "success": bool(manifest.get("success", False)),
        "warnings": list(manifest.get("warnings") or []),
        "errors": list(manifest.get("errors") or []),
        "manifest": manifest,
    }


def list_runtime_backups(
    *,
    backup_root_value: str | Path | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    backup_root = resolve_backup_root(backup_root_value)
    if not backup_root.exists():
        return []

    bundles = sorted(
        backup_root.rglob("runtime_backup_*.zip"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )

    results: list[dict[str, Any]] = []
    for bundle_path in bundles[: max(1, int(limit))]:
        try:
            manifest = _read_manifest_from_zip(bundle_path)
            results.append(_bundle_summary_from_manifest(bundle_path, manifest))
        except Exception as exc:  # pragma: no cover - defensive summary path
            results.append(
                {
                    "bundle_path": str(bundle_path),
                    "bundle_name": bundle_path.name,
                    "bundle_size_bytes": bundle_path.stat().st_size if bundle_path.exists() else 0,
                    "created_at": None,
                    "backup_root": str(backup_root),
                    "asset_count": 0,
                    "captured_asset_count": 0,
                    "failed_asset_count": 0,
                    "skipped_asset_count": 0,
                    "success": False,
                    "warnings": [],
                    "errors": [str(exc)],
                    "manifest": None,
                }
            )

    return results


def latest_runtime_backup_summary(
    *,
    backup_root_value: str | Path | None = None,
) -> dict[str, Any]:
    backups = list_runtime_backups(backup_root_value=backup_root_value, limit=1)
    return backups[0] if backups else {}


def prune_runtime_backups(
    *,
    backup_root_value: str | Path | None = None,
    retention_days: int = 90,
) -> list[Path]:
    backup_root = resolve_backup_root(backup_root_value)
    if not backup_root.exists() or retention_days <= 0:
        return []

    cutoff_ts = now_dt().timestamp() - (int(retention_days) * 86400)
    deleted: list[Path] = []
    for bundle_path in backup_root.rglob("runtime_backup_*.zip"):
        try:
            modified_ts = bundle_path.stat().st_mtime
        except OSError:
            continue
        if modified_ts >= cutoff_ts:
            continue
        try:
            bundle_path.unlink()
        except OSError:
            continue
        deleted.append(bundle_path)

    return deleted


def create_runtime_backup(
    *,
    backup_dir_value: str | Path | None = None,
    backup_retention_days: int = 90,
    include_env_file: bool = False,
    env_path: Path | None = None,
) -> dict[str, Any]:
    created_at = now_dt()
    backup_root = resolve_backup_root(backup_dir_value)
    date_dir = backup_root / created_at.strftime("%Y/%m/%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    sources = _collect_existing_sources(
        include_env_file=include_env_file,
        env_path=env_path,
    )

    assets: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    with tempfile.TemporaryDirectory(prefix="runtime_backup_", dir=date_dir) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        for entry in sources:
            source_path = Path(entry["source_path"])
            bundle_path = str(entry["bundle_path"])
            asset: dict[str, Any] = {
                "kind": entry["kind"],
                "source_path": str(source_path),
                "bundle_path": bundle_path,
                "restore_path": str(entry["restore_path"]),
                "required": bool(entry.get("required", False)),
                "status": "pending",
                "size_bytes": 0,
            }

            if not source_path.exists():
                asset["status"] = "missing" if asset["required"] else "skipped"
                note = f"{entry['kind']} not found at {source_path}"
                if asset["required"]:
                    errors.append(note)
                    asset["error"] = note
                else:
                    warnings.append(note)
                    asset["note"] = note
                assets.append(asset)
                continue

            temp_asset_path = temp_dir / bundle_path
            try:
                if entry["kind"] == "sqlite_db":
                    size_bytes = _snapshot_sqlite_database(source_path, temp_asset_path)
                else:
                    size_bytes = _copy_regular_file(source_path, temp_asset_path)
                asset["status"] = "captured"
                asset["size_bytes"] = int(size_bytes)
            except Exception as exc:
                asset["status"] = "failed"
                asset["error"] = str(exc)
                errors.append(f"{entry['kind']}: {exc}")
            assets.append(asset)

        bundle_name = _build_bundle_name(created_at)
        bundle_path = date_dir / bundle_name
        temp_bundle_path = bundle_path.with_name(f"{bundle_path.name}.tmp")
        manifest = {
            "version": 1,
            "created_at": created_at.isoformat(timespec="seconds"),
            "backup_root": str(backup_root),
            "bundle_path": str(bundle_path),
            "bundle_name": bundle_name,
            "backup_retention_days": int(backup_retention_days),
            "include_env_file": bool(include_env_file),
            "sqlite_snapshot_method": "sqlite_backup_api",
            "assets": assets,
            "warnings": warnings,
            "errors": errors,
        }
        _write_manifest(temp_dir / "manifest.json", manifest)
        if errors:
            return {
                "success": False,
                "created_at": manifest["created_at"],
                "backup_root": str(backup_root),
                "bundle_path": str(bundle_path),
                "bundle_name": bundle_name,
                "bundle_size_bytes": 0,
                "assets": assets,
                "warnings": warnings,
                "errors": errors,
                "pruned_backups": [],
            }
        try:
            _zip_bundle_path(temp_bundle_path, temp_dir, assets)
            os.replace(temp_bundle_path, bundle_path)
        except Exception as exc:
            return {
                "success": False,
                "created_at": manifest["created_at"],
                "backup_root": str(backup_root),
                "bundle_path": str(bundle_path),
                "bundle_name": bundle_name,
                "bundle_size_bytes": 0,
                "assets": assets,
                "warnings": warnings,
                "errors": [str(exc)],
                "pruned_backups": [],
            }

    pruned = prune_runtime_backups(
        backup_root_value=backup_root,
        retention_days=int(backup_retention_days),
    )
    return {
        "success": True,
        "created_at": manifest["created_at"],
        "backup_root": str(backup_root),
        "bundle_path": str(bundle_path),
        "bundle_name": bundle_name,
        "bundle_size_bytes": bundle_path.stat().st_size if bundle_path.exists() else 0,
        "assets": assets,
        "warnings": warnings,
        "errors": [],
        "pruned_backups": [str(path) for path in pruned],
    }


def _pre_restore_backup(target_path: Path, temp_dir: Path) -> Path | None:
    if not target_path.exists():
        return None
    pre_restore_dir = temp_dir / "pre_restore"
    pre_restore_dir.mkdir(parents=True, exist_ok=True)
    backup_path = pre_restore_dir / target_path.name
    shutil.copy2(target_path, backup_path)
    return backup_path


def _restore_file(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target_path.with_name(f"{target_path.name}.restore.tmp")
    shutil.copy2(source_path, temp_target)
    last_error: Exception | None = None
    try:
        for _ in range(10):
            try:
                os.replace(temp_target, target_path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.1)
            except OSError as exc:
                last_error = exc
                time.sleep(0.1)

        if last_error is not None:
            raise last_error
        os.replace(temp_target, target_path)
    finally:
        if temp_target.exists():
            try:
                temp_target.unlink()
            except OSError:
                pass


def restore_runtime_backup(
    *,
    bundle_path_value: str | Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    bundle_path = Path(bundle_path_value).expanduser()
    if not bundle_path.is_absolute():
        bundle_path = PROJECT_ROOT / bundle_path

    if not bundle_path.exists():
        return {
            "success": False,
            "bundle_path": str(bundle_path),
            "errors": [f"Backup bundle not found: {bundle_path}"],
            "warnings": [],
            "restored_assets": [],
        }

    try:
        manifest = _read_manifest_from_zip(bundle_path)
    except Exception as exc:
        return {
            "success": False,
            "bundle_path": str(bundle_path),
            "errors": [str(exc)],
            "warnings": [],
            "restored_assets": [],
        }

    assets = list(manifest.get("assets") or [])
    errors: list[str] = []
    warnings: list[str] = []
    restored_assets: list[dict[str, Any]] = []

    try:
        with tempfile.TemporaryDirectory(prefix="runtime_restore_") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            with zipfile.ZipFile(bundle_path, "r") as archive:
                for asset in assets:
                    status = str(asset.get("status") or "")
                    if status != "captured":
                        continue

                    archive_path = str(asset.get("bundle_path") or "").strip()
                    restore_path_text = str(asset.get("restore_path") or "").strip()
                    if not archive_path or not restore_path_text:
                        errors.append(f"Invalid manifest entry for {asset.get('kind', 'unknown')}")
                        continue
                    restore_path = Path(restore_path_text)

                    if restore_path.exists() and not overwrite:
                        errors.append(
                            f"Refusing to overwrite existing file without overwrite=True: {restore_path}"
                        )
                        continue

                    extracted_path = temp_dir / archive_path
                    extracted_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        archive.extract(archive_path, path=temp_dir)
                    except KeyError:
                        errors.append(f"Archive entry missing: {archive_path}")
                        continue

                    if restore_path.exists():
                        _pre_restore_backup(restore_path, temp_dir)

                    try:
                        _restore_file(extracted_path, restore_path)
                    except Exception as exc:
                        errors.append(f"{asset.get('kind', 'unknown')}: {exc}")
                        continue

                    if str(asset.get("kind")) == "sqlite_db":
                        for suffix in ("-wal", "-shm"):
                            sidecar = Path(f"{restore_path}{suffix}")
                            if sidecar.exists():
                                try:
                                    sidecar.unlink()
                                except OSError:
                                    pass

                    restored_assets.append(
                        {
                            "kind": asset.get("kind"),
                            "restore_path": str(restore_path),
                            "bundle_path": archive_path,
                        }
                    )
    except Exception as exc:
        errors.append(str(exc))

    return {
        "success": not errors,
        "bundle_path": str(bundle_path),
        "created_at": manifest.get("created_at"),
        "backup_root": manifest.get("backup_root"),
        "restored_assets": restored_assets,
        "warnings": warnings or list(manifest.get("warnings") or []),
        "errors": errors,
    }
