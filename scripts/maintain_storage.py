#!/usr/bin/env python3
"""Back up live SQLite, verify/restore into a NEW file, and expire only our backups/check results."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bakend"))
from app.maintenance import backup_database, expire_operations, retain_backups, restore_database, verify_backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("--directory", type=Path, required=True)
    backup.add_argument("--keep", type=int, default=14)
    backup.add_argument("--expire-checks-days", type=int, default=14)
    for action in ("verify", "restore"):
        command = sub.add_parser(action)
        command.add_argument("--backup", type=Path, required=True)
        if action == "restore":
            command.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "backup":
        if args.keep < 2 or args.expire_checks_days < 1:
            parser.error("keep >= 2 and expire-checks-days >= 1 required")
        result = backup_database(args.database, args.directory)
        result["expired_backups"] = retain_backups(args.directory, args.keep, apply=True)
        result["expired_checks"] = expire_operations(args.database, args.expire_checks_days)
    elif args.action == "verify":
        result = verify_backup(args.backup)
    else:
        result = restore_database(args.backup, args.destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
