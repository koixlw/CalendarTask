"""CLI 工具 — 从 DesktopCal 数据库导入到 PyCal。

Usage:
    python -m pycal.importer.cli
    python -m pycal.importer.cli --source /path/to/calendar.db
"""

from __future__ import annotations
import argparse
from pathlib import Path

from pycal.db.connection import default_db_path, desktopcal_db_path
from pycal.importer import import_from_desktopcal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(desktopcal_db_path()),
        help="DesktopCal 数据库路径",
    )
    parser.add_argument("--target", default=None)
    args = parser.parse_args()

    target = Path(args.target) if args.target else default_db_path()
    print(f"[i] reading from: {args.source}")
    print(f"[i] writing to:   {target}")
    n = import_from_desktopcal(args.source, target)
    print(f"[ok] imported {n} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
