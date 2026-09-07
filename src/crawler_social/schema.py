"""SQLite schema for crawler-social: five tables only."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'interrupted')),
    error        TEXT
);

-- Markup is never stored, only the parsed text in posts and comments.
-- sha256 is over the captured bytes, so an unchanged re-capture is recognisable.
CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES runs(id),
    page_url     TEXT NOT NULL,
    captured_at  TEXT NOT NULL,
    sha256       TEXT NOT NULL UNIQUE,
    size_bytes   INTEGER
);

CREATE TABLE IF NOT EXISTS posts (
    post_id       TEXT PRIMARY KEY,
    page_url      TEXT NOT NULL,
    text          TEXT,
    author        TEXT,
    published_at  TEXT,
    post_url      TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);

-- rank_index is 1-based order on the permalink page, which Facebook sorts by
-- "most relevant". Replies are not stored, only top-level comments.
CREATE TABLE IF NOT EXISTS comments (
    comment_id    TEXT PRIMARY KEY,
    post_id       TEXT NOT NULL REFERENCES posts(post_id),
    page_url      TEXT NOT NULL,
    author        TEXT,
    text          TEXT,
    published_at  TEXT,
    like_count    INTEGER,
    rank_index    INTEGER NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    page_url        TEXT PRIMARY KEY,
    last_post_id    TEXT,
    last_post_time  TEXT,
    updated_at      TEXT NOT NULL
);

-- Viewer indexes, created by db.connect() and never by the server.
CREATE INDEX IF NOT EXISTS idx_posts_page_url ON posts(page_url);
CREATE INDEX IF NOT EXISTS idx_posts_sort ON posts(COALESCE(published_at, last_seen));
CREATE INDEX IF NOT EXISTS idx_snapshots_run ON snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_page ON snapshots(page_url);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id, rank_index);
"""
