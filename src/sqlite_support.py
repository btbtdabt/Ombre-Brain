"""Small SQLite connection primitives shared by independent runtime stores."""

from __future__ import annotations

import sqlite3
from os import PathLike


def connect_rows(path: str | bytes | PathLike[str] | PathLike[bytes], *, timeout: float = 5.0) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=timeout)
    connection.row_factory = sqlite3.Row
    return connection
