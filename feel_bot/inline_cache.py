from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InlineCache:
    db_path: Path
    max_rows: int = 5000

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inline_cache (
                k TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inline_cache_created_at ON inline_cache(created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kv_store (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            )
            """
        )
        return conn

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT v FROM kv_store WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store(k, v) VALUES (?, ?)", (key, value)
            )
            conn.commit()

    def get(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT file_id FROM inline_cache WHERE k = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def put(self, key: str, file_id: str) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO inline_cache(k, file_id, created_at) VALUES (?, ?, ?)",
                (key, file_id, now),
            )
            # Prune: keep most recent max_rows
            conn.execute(
                """
                DELETE FROM inline_cache
                WHERE k IN (
                    SELECT k FROM inline_cache
                    ORDER BY created_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self.max_rows,),
            )
            conn.commit()


def default_inline_cache() -> InlineCache:
    p = Path(
        os.environ.get("FEEL_CACHE_DB", "./feel_inline_cache.sqlite3")
    ).expanduser()
    return InlineCache(db_path=p)
