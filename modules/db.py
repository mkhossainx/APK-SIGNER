"""
modules/db.py
-------------
Minimal SQLite persistence layer. No ORM is used on purpose to keep the
app lightweight and easy to audit. All queries use parameterized
placeholders (never string-formatted SQL) to prevent injection.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS builds (
    id              TEXT PRIMARY KEY,
    original_name   TEXT NOT NULL,
    sha256_original TEXT,
    sha256_signed   TEXT,
    sign_type       TEXT NOT NULL,        -- 'debug' or 'custom'
    status          TEXT NOT NULL,        -- pending, running, success, failed
    signed_path     TEXT,
    log_path        TEXT,
    verify_json     TEXT,
    error_message   TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS keystores (
    id              TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    path            TEXT NOT NULL,
    alias           TEXT,
    common_name     TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def get_conn(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------- builds --

def insert_build(db_path: Path, **fields) -> None:
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    with get_conn(db_path) as conn:
        conn.execute(
            f"INSERT INTO builds ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        conn.commit()


def update_build(db_path: Path, build_id: str, **fields) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    with get_conn(db_path) as conn:
        conn.execute(
            f"UPDATE builds SET {set_clause} WHERE id = ?",
            (*fields.values(), build_id),
        )
        conn.commit()


def get_build(db_path: Path, build_id: str) -> Optional[sqlite3.Row]:
    with get_conn(db_path) as conn:
        cur = conn.execute("SELECT * FROM builds WHERE id = ?", (build_id,))
        return cur.fetchone()


def list_builds(db_path: Path, limit: int = 50):
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM builds ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()


# ------------------------------------------------------------- keystores --

def insert_keystore(db_path: Path, **fields) -> None:
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    with get_conn(db_path) as conn:
        conn.execute(
            f"INSERT INTO keystores ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        conn.commit()


def get_keystore(db_path: Path, keystore_id: str) -> Optional[sqlite3.Row]:
    with get_conn(db_path) as conn:
        cur = conn.execute("SELECT * FROM keystores WHERE id = ?", (keystore_id,))
        return cur.fetchone()


def list_keystores(db_path: Path, limit: int = 50):
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM keystores ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()
