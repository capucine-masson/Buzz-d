import secrets
import sqlite3

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans O/0/I/1 pour éviter les confusions
CODE_LENGTH = 5
MAX_CODE_ATTEMPTS = 20


def generate_room_code(conn: sqlite3.Connection) -> str:
    for _ in range(MAX_CODE_ATTEMPTS):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        exists = conn.execute("SELECT 1 FROM rooms WHERE code = ?", (code,)).fetchone()
        if not exists:
            return code
    raise RuntimeError("Impossible de générer un code de room unique")


def create_room(conn: sqlite3.Connection, host_nickname: str) -> str:
    code = generate_room_code(conn)
    conn.execute(
        "INSERT INTO rooms (code, host_nickname, status) VALUES (?, ?, 'lobby')",
        (code, host_nickname),
    )
    conn.execute(
        "INSERT INTO players (room_code, nickname) VALUES (?, ?)",
        (code, host_nickname),
    )
    conn.commit()
    return code


def get_room(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM rooms WHERE code = ?", (code,)).fetchone()


def list_players(conn: sqlite3.Connection, code: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT nickname, score FROM players WHERE room_code = ? ORDER BY id",
        (code,),
    ).fetchall()


def add_player(conn: sqlite3.Connection, code: str, nickname: str) -> None:
    conn.execute(
        "INSERT INTO players (room_code, nickname) VALUES (?, ?)",
        (code, nickname),
    )
    conn.commit()


def save_tracks(conn: sqlite3.Connection, code: str, tracks: list[dict]) -> None:
    conn.execute("DELETE FROM tracks WHERE room_code = ?", (code,))
    conn.executemany(
        """INSERT INTO tracks (room_code, position, deezer_track_id, title, artist, preview_url)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (code, index, track["deezer_track_id"], track["title"], track["artist"], track["preview_url"])
            for index, track in enumerate(tracks)
        ],
    )
    conn.commit()


def count_tracks(conn: sqlite3.Connection, code: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS total FROM tracks WHERE room_code = ?", (code,)).fetchone()
    return row["total"] if row else 0
