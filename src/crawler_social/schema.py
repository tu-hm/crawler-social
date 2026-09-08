"""SQLite schema for crawler-social: five tables only.

Tables and indexes are separate scripts because they run at different times.
`CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a
column added in a later version arrives through an ALTER in `db._migrate_columns`
-- and an index on that column cannot be created until the ALTER has run. So
`db.connect` creates the tables, migrates the columns, and only then creates
the indexes.
"""

TABLES_SQL = """
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

-- reaction_count is the total across every emotion, NULL when the capture
-- showed no count: Facebook omits the summary entirely at zero reactions.
CREATE TABLE IF NOT EXISTS posts (
    post_id        TEXT PRIMARY KEY,
    page_url       TEXT NOT NULL,
    text           TEXT,
    author         TEXT,
    published_at   TEXT,
    post_url       TEXT,
    reaction_count INTEGER,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL
);

-- rank_index is 1-based order on the permalink page, which Facebook sorts by
-- "most relevant". It is scoped to the siblings: a top-level comment ranks
-- among the post's comments, a reply among the replies to its own parent.
-- parent_comment_id is NULL at top level and names the answered comment
-- otherwise. It carries no foreign key on purpose -- a reply can be captured
-- in a run whose parent was dropped past the comment limit, and losing the
-- reply to enforce the reference would be the worse trade.
CREATE TABLE IF NOT EXISTS comments (
    comment_id        TEXT PRIMARY KEY,
    post_id           TEXT NOT NULL REFERENCES posts(post_id),
    page_url          TEXT NOT NULL,
    author            TEXT,
    text              TEXT,
    published_at      TEXT,
    like_count        INTEGER,
    rank_index        INTEGER NOT NULL,
    parent_comment_id TEXT,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    page_url        TEXT PRIMARY KEY,
    last_post_id    TEXT,
    last_post_time  TEXT,
    updated_at      TEXT NOT NULL
);
"""

#: Created after _migrate_columns, never by the server.
INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_posts_page_url ON posts(page_url);
CREATE INDEX IF NOT EXISTS idx_posts_sort ON posts(COALESCE(published_at, last_seen));
CREATE INDEX IF NOT EXISTS idx_snapshots_run ON snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_page ON snapshots(page_url);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id, rank_index);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_comment_id);
"""

#: Both scripts, for callers that want the whole schema in one string.
SCHEMA_SQL = TABLES_SQL + INDEXES_SQL
