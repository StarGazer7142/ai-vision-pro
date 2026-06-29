from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.maintenance_service import cleanup_runtime, collect_health, create_backup


def main() -> int:
    parser = argparse.ArgumentParser(description="AI platform maintenance helper")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Print local health and disk summary")

    backup = sub.add_parser("backup", help="Create a zip backup under data/backups")
    backup.add_argument("--include-videos", action="store_true", help="Include replay/upload video files")

    cleanup = sub.add_parser("cleanup", help="Clean runtime files by retention policy")
    cleanup.add_argument("--retention-days", type=int, default=30)
    cleanup.add_argument("--replay-retention-days", type=int, default=30)
    cleanup.add_argument("--backup-retention-days", type=int, default=90)
    cleanup.add_argument("--apply", action="store_true", help="Actually delete files; default is dry-run")

    args = parser.parse_args()
    if args.command == "health":
        payload = collect_health()
    elif args.command == "backup":
        payload = create_backup(include_videos=args.include_videos)
    else:
        payload = cleanup_runtime(
            retention_days=args.retention_days,
            replay_retention_days=args.replay_retention_days,
            backup_retention_days=args.backup_retention_days,
            dry_run=not args.apply,
        )

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
