"""Copy worlds and read them as wf-core `Snapshot`s.

A world is a single SQLite file, so a fork (reset) is a file copy: about a millisecond for this
world, with no shared state between episodes.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from wf_core.models import Snapshot

from .schema import SCHEMA


def fork(src: Path, dst: Path) -> Path:
    """Copy the world file `src` to `dst` and return `dst`."""
    shutil.copyfile(src, dst)
    return dst


def read_snapshot(conn: sqlite3.Connection) -> Snapshot:
    """Every table in `SCHEMA` as {table: {str(pk): row}}, rows ordered by primary key."""
    tables: dict[str, dict[str, dict]] = {}
    for name, info in SCHEMA.tables.items():
        cursor = conn.execute(f"SELECT * FROM {name} ORDER BY {info.pk}")
        columns = [d[0] for d in cursor.description]
        key = columns.index(info.pk)
        tables[name] = {str(row[key]): dict(zip(columns, row, strict=True)) for row in cursor}
    return Snapshot(schema_version=SCHEMA.version, tables=tables)
