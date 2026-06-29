from __future__ import annotations

import os
import platform
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from backend.app.core.config import CONFIG_DIR, DATA_DIR, DEFAULT_DB_PATH, PROJECT_ROOT, RUNTIME_DIR
from backend.app.core.logging import LOG_DIR


BACKUP_DIR = DATA_DIR / "backups"
CLEANUP_TARGETS = (
    DATA_DIR / "outputs",
    DATA_DIR / "replay_clips",
    DATA_DIR / "uploads" / "videos",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_relative(path: Path) -> str:
    return str(path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/")


def _iter_existing_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            yield path
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file():
                    yield child


def collect_health() -> dict:
    disk = shutil.disk_usage(PROJECT_ROOT)
    log_files = []
    if LOG_DIR.exists():
        for item in sorted(LOG_DIR.glob("*.log*")):
            if item.is_file():
                log_files.append(
                    {
                        "name": item.name,
                        "size_bytes": item.stat().st_size,
                        "modified_at": datetime.fromtimestamp(item.stat().st_mtime).isoformat(),
                    }
                )

    return {
        "status": "ok",
        "generated_at": _utcnow().isoformat(),
        "app_env": os.getenv("APP_ENV", "development"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "paths": {
            "project_root": str(PROJECT_ROOT),
            "database": str(DEFAULT_DB_PATH),
            "config_dir": str(CONFIG_DIR),
            "runtime_dir": str(RUNTIME_DIR),
            "backup_dir": str(BACKUP_DIR),
        },
        "checks": {
            "database_exists": DEFAULT_DB_PATH.exists(),
            "config_dir_exists": CONFIG_DIR.exists(),
            "runtime_dir_exists": RUNTIME_DIR.exists(),
            "ffmpeg_available": bool(shutil.which("ffmpeg")),
        },
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "free_ratio": round(disk.free / max(disk.total, 1), 4),
        },
        "logs": log_files[:50],
    }


def create_backup(*, include_videos: bool = False) -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _utcnow().strftime("%Y%m%d_%H%M%S")
    archive_path = BACKUP_DIR / f"ai_platform_backup_{stamp}.zip"

    sources = [CONFIG_DIR, DEFAULT_DB_PATH, LOG_DIR]
    if include_videos:
        sources.extend(CLEANUP_TARGETS)

    added = []
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in _iter_existing_files(sources):
            try:
                arcname = _safe_relative(file_path)
            except ValueError:
                arcname = file_path.name
            zf.write(file_path, arcname)
            added.append(arcname)

        manifest = "\n".join(
            [
                f"created_at={_utcnow().isoformat()}",
                f"include_videos={include_videos}",
                f"file_count={len(added)}",
            ]
        )
        zf.writestr("backup_manifest.txt", manifest)

    return {
        "ok": True,
        "backup_path": str(archive_path),
        "backup_name": archive_path.name,
        "include_videos": include_videos,
        "file_count": len(added),
        "size_bytes": archive_path.stat().st_size,
    }


def cleanup_runtime(
    *,
    retention_days: int = 30,
    replay_retention_days: int = 30,
    backup_retention_days: int = 90,
    dry_run: bool = True,
) -> dict:
    now = _utcnow()
    targets = [
        (DATA_DIR / "outputs", retention_days),
        (DATA_DIR / "replay_clips", replay_retention_days),
        (DATA_DIR / "uploads" / "videos", replay_retention_days),
        (BACKUP_DIR, backup_retention_days),
    ]

    candidates = []
    deleted = []
    for root, days in targets:
        if not root.exists():
            continue
        cutoff = now - timedelta(days=max(1, int(days)))
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            modified_at = datetime.utcfromtimestamp(file_path.stat().st_mtime)
            if modified_at >= cutoff:
                continue
            item = {
                "path": str(file_path),
                "size_bytes": file_path.stat().st_size,
                "modified_at": modified_at.isoformat(),
                "retention_days": int(days),
            }
            candidates.append(item)
            if not dry_run:
                file_path.unlink(missing_ok=True)
                deleted.append(item)

    return {
        "ok": True,
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "deleted_count": len(deleted),
        "candidate_size_bytes": sum(item["size_bytes"] for item in candidates),
        "deleted_size_bytes": sum(item["size_bytes"] for item in deleted),
        "items": candidates[:500],
    }
