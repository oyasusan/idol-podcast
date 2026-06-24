import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS news (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    category        TEXT    NOT NULL DEFAULT 'general',
    source          TEXT    NOT NULL,
    group_name      TEXT,
    title           TEXT    NOT NULL,
    url             TEXT,
    summary         TEXT,
    published_at    TEXT,
    used_in_episode INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(url)
);

CREATE TABLE IF NOT EXISTS analysis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL UNIQUE,
    scene_summary   TEXT,
    spotlight_topics TEXT,
    member_changes  TEXT,
    upcoming_events TEXT,
    trending_themes TEXT,
    keywords        TEXT,
    raw_prompt      TEXT,
    model_used      TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL UNIQUE,
    title           TEXT    NOT NULL,
    script_path     TEXT,
    audio_path      TEXT,
    duration_seconds INTEGER,
    file_size_bytes INTEGER,
    rss_published   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_news_date     ON news(date);
CREATE INDEX IF NOT EXISTS idx_news_category ON news(category);
CREATE INDEX IF NOT EXISTS idx_episodes_date ON episodes(date);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
    logger.info(f"データベース初期化完了: {db_path}")
