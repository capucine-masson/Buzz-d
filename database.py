import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "buzzd.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    code TEXT PRIMARY KEY,
    playlist_url TEXT,
    status TEXT NOT NULL DEFAULT 'lobby',
    current_track_index INTEGER NOT NULL DEFAULT -1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_code TEXT NOT NULL REFERENCES rooms(code) ON DELETE CASCADE,
    nickname TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(room_code, nickname)
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_code TEXT NOT NULL REFERENCES rooms(code) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    deezer_track_id INTEGER,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    preview_url TEXT NOT NULL,
    played INTEGER NOT NULL DEFAULT 0
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
