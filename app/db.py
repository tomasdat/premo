import os
import shutil
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS accounts (
    puuid TEXT PRIMARY KEY,
    game_name TEXT NOT NULL,
    tag_line TEXT NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    queue_id INTEGER NOT NULL,
    game_creation INTEGER NOT NULL,
    duration INTEGER NOT NULL,
    version TEXT
);
CREATE INDEX IF NOT EXISTS idx_matches_queue ON matches(queue_id, game_creation);
CREATE TABLE IF NOT EXISTS participants (
    match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    puuid TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    champion TEXT NOT NULL,
    position TEXT,
    kills INTEGER, deaths INTEGER, assists INTEGER,
    cs INTEGER, damage INTEGER, gold INTEGER, vision INTEGER,
    win INTEGER NOT NULL,
    riot_name TEXT,
    PRIMARY KEY (match_id, puuid)
);
CREATE INDEX IF NOT EXISTS idx_part_puuid ON participants(puuid);
CREATE TABLE IF NOT EXISTS ranks (
    puuid TEXT PRIMARY KEY, solo INTEGER, flex INTEGER, fetched_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ignored_suggestions (
    puuid TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash BLOB NOT NULL,
    salt BLOB NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    player_id INTEGER REFERENCES players(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    used_at INTEGER
);
CREATE TABLE IF NOT EXISTS squads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(owner_id, name)
);
CREATE TABLE IF NOT EXISTS squad_players (
    squad_id INTEGER NOT NULL REFERENCES squads(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    PRIMARY KEY (squad_id, player_id)
);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(participants)")}
    for col, typ in [("items", "TEXT"), ("keystone", "INTEGER"), ("sub_style", "INTEGER"),
                     ("spell1", "INTEGER"), ("spell2", "INTEGER"), ("level", "INTEGER")]:
        if col not in have:
            conn.execute(f"ALTER TABLE participants ADD COLUMN {col} {typ}")
    if "used_at" not in {r[1] for r in conn.execute("PRAGMA table_info(invites)")}:
        conn.execute("ALTER TABLE invites ADD COLUMN used_at INTEGER")
    if "owner_id" not in {r[1] for r in conn.execute("PRAGMA table_info(squads)")}:
        _migrate_squads(conn, path)
    conn.commit()
    return conn


def _migrate_squads(conn: sqlite3.Connection, path: str) -> None:
    """Squads become per-account: rebuild the table (SQLite cannot drop the old UNIQUE(name))."""
    conn.commit()
    if path != ":memory:" and os.path.exists(path):
        shutil.copy(path, path + ".bak-pre-accounts")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        BEGIN;
        CREATE TABLE squads_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(owner_id, name)
        );
        INSERT INTO squads_new(id, name) SELECT id, name FROM squads;
        DROP TABLE squads;
        ALTER TABLE squads_new RENAME TO squads;
        COMMIT;
    """)
    conn.execute("PRAGMA foreign_keys = ON")
