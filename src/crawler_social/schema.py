"""SQLite schema for crawler-social: four tables only."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'interrupted')),
    error        TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES runs(id),
    page_url     TEXT NOT NULL,
    captured_at  TEXT NOT NULL,
    sha256       TEXT NOT NULL UNIQUE,
    html         BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    post_id       TEXT PRIMARY KEY,
    page_url      TEXT NOT NULL,
    text          TEXT,
    author        TEXT,
    published_at  TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    page_url        TEXT PRIMARY KEY,
    last_post_id    TEXT,
    last_post_time  TEXT,
    updated_at      TEXT NOT NULL
);
"""
