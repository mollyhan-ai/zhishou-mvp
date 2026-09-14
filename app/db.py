"""SQLite storage.

Tables
------
sessions     one consult: audio -> transcript -> note
kb_docs      imported knowledge documents (metadata kept verbatim)
kb_chunks    retrievable passages, each traceable back to a doc + locator
event_log    de-identified analytics. No clinical text by default.
raw_queries  opt-in only, retention-bounded, separate from event_log
"""
import os
import json
import sqlite3
import time
import uuid

from .config import get_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    status          TEXT NOT NULL,           -- created|transcribing|transcribed|generating|drafted|failed
    error_message   TEXT,
    error_kind      TEXT,                    -- config|provider|input
    audio_path      TEXT,
    audio_name      TEXT,
    audio_bytes     INTEGER,
    transcript      TEXT,
    transcript_edited_at REAL,
    note_json       TEXT,
    note_generated_at    REAL,
    note_edited_at       REAL,
    flags_json      TEXT,
    confirmed_at    REAL,
    confirmed_by    TEXT
);

CREATE TABLE IF NOT EXISTS pets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    species TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_docs (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    source        TEXT NOT NULL,
    version       TEXT,
    published_on  TEXT,
    license_note  TEXT NOT NULL,
    topics        TEXT,                      -- JSON list
    species       TEXT,                      -- JSON list
    imported_at   REAL NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES kb_docs(id) ON DELETE CASCADE,
    locator     TEXT NOT NULL,               -- e.g. "3.2 补液" or "p.45"
    ordinal     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    tokens_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON kb_chunks(doc_id);

CREATE TABLE IF NOT EXISTS event_log (
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    event       TEXT NOT NULL,
    session_ref TEXT,                        -- salted hash, not the session id
    payload     TEXT NOT NULL                -- JSON, de-identified metrics only
);

CREATE TABLE IF NOT EXISTS feature_requests (
    id            TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL,
    text_scrubbed TEXT NOT NULL,
    pii_kinds     TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS raw_queries (
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    query_text  TEXT NOT NULL,
    consented   INTEGER NOT NULL DEFAULT 1
);
"""


def connect():
    cfg = get_config()
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # Additive migration preserves every existing consultation and confirmation.
        conn.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        for name, definition in {
            "pet_id": "TEXT REFERENCES pets(id)",
            "custom_summary": "TEXT",
            "summary_revision": "INTEGER NOT NULL DEFAULT 0",
            "history_raw": "TEXT NOT NULL DEFAULT ''",
            "history_summary_json": "TEXT NOT NULL DEFAULT '{}'",
            "history_summary_stale": "INTEGER NOT NULL DEFAULT 0",
            "evidence_revision": "INTEGER NOT NULL DEFAULT 0",
            "note_revision": "INTEGER NOT NULL DEFAULT 0",
            "note_needs_review": "INTEGER NOT NULL DEFAULT 0",
            "audio_origin": "TEXT",
            "recorded_at": "REAL",
            "audio_uploaded_at": "REAL",
            "audio_duration_seconds": "REAL",
            "asr_used_language": "TEXT",
            "asr_used_provider": "TEXT",
            "asr_used_model": "TEXT",
            "llm_used_model": "TEXT",
            "last_content_edited_at": "REAL",
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_pet ON sessions(pet_id)")
        from .patient_records import migrate
        migrate(conn)
        conn.commit()
    finally:
        conn.close()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# --- sessions --------------------------------------------------------------

def create_session() -> str:
    sid = new_id("s")
    now = time.time()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO sessions (id, created_at, updated_at, status) VALUES (?,?,?,?)",
            (sid, now, now, "created"),
        )
        conn.commit()
    finally:
        conn.close()
    return sid


# Display metadata only; confirmation and evidence revisions are unchanged.
CONTENT_FIELDS = {"transcript", "note_json", "history_raw", "history_summary_json", "audio_path"}


def update_session(session_id: str, **fields):
    if not fields:
        return
    fields["updated_at"] = time.time()
    if CONTENT_FIELDS.intersection(fields):
        fields["last_content_edited_at"] = fields["updated_at"]
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = connect()
    try:
        conn.execute(f"UPDATE sessions SET {cols} WHERE id = ?", (*fields.values(), session_id))
        conn.commit()
    finally:
        conn.close()


def get_session(session_id: str):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_sessions(limit: int = 50):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, created_at, status, confirmed_at, audio_name FROM sessions "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    """Hard delete: audio file, transcript and note all go."""
    sess = get_session(session_id)
    if not sess:
        return False
    path = sess.get("audio_path")
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    conn = connect()
    try:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()
    return True


def delete_session_part(session_id: str, part: str) -> bool:
    """part in {audio, transcript, note}. Deleting an upstream part clears
    nothing downstream automatically -- the vet decides."""
    sess = get_session(session_id)
    if not sess:
        return False
    if part == "audio":
        path = sess.get("audio_path")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        update_session(session_id, audio_path=None, audio_name=None, audio_bytes=None,
                       audio_origin=None, recorded_at=None, audio_uploaded_at=None, audio_duration_seconds=None)
    elif part == "transcript":
        update_session(session_id, transcript=None, transcript_edited_at=None)
    elif part == "note":
        update_session(
            session_id, note_json=None, flags_json=None,
            note_generated_at=None, note_edited_at=None,
            confirmed_at=None, confirmed_by=None,
        )
    else:
        return False
    return True


# --- knowledge base --------------------------------------------------------

def insert_doc(doc: dict) -> str:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO kb_docs (id,title,source,version,published_on,license_note,"
            "topics,species,imported_at,active) VALUES (?,?,?,?,?,?,?,?,?,1)",
            (
                doc["id"], doc["title"], doc["source"], doc.get("version"),
                doc.get("published_on"), doc["license_note"],
                json.dumps(doc.get("topics", []), ensure_ascii=False),
                json.dumps(doc.get("species", []), ensure_ascii=False),
                time.time(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return doc["id"]


def insert_chunks(chunks: list):
    conn = connect()
    try:
        conn.executemany(
            "INSERT INTO kb_chunks (id,doc_id,locator,ordinal,text,tokens_json) VALUES (?,?,?,?,?,?)",
            [(c["id"], c["doc_id"], c["locator"], c["ordinal"], c["text"],
              json.dumps(c["tokens"], ensure_ascii=False)) for c in chunks],
        )
        conn.commit()
    finally:
        conn.close()


def list_docs(active_only: bool = True):
    conn = connect()
    try:
        q = "SELECT * FROM kb_docs"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY imported_at DESC"
        rows = conn.execute(q).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["topics"] = json.loads(d["topics"] or "[]")
            d["species"] = json.loads(d["species"] or "[]")
            out.append(d)
        return out
    finally:
        conn.close()


def set_doc_active(doc_id: str, active: bool) -> bool:
    conn = connect()
    try:
        cur = conn.execute("UPDATE kb_docs SET active = ? WHERE id = ?", (1 if active else 0, doc_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_doc(doc_id: str) -> bool:
    conn = connect()
    try:
        conn.execute("DELETE FROM kb_chunks WHERE doc_id = ?", (doc_id,))
        cur = conn.execute("DELETE FROM kb_docs WHERE id = ?", (doc_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def active_chunks():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT c.*, d.title, d.source, d.version, d.published_on "
            "FROM kb_chunks c JOIN kb_docs d ON d.id = c.doc_id WHERE d.active = 1"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tokens"] = json.loads(d["tokens_json"])
            out.append(d)
        return out
    finally:
        conn.close()


def get_chunk(chunk_id: str):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT c.*, d.title, d.source, d.version, d.published_on, d.license_note "
            "FROM kb_chunks c JOIN kb_docs d ON d.id = c.doc_id WHERE c.id = ?",
            (chunk_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --- logging ---------------------------------------------------------------

def insert_event(event: str, session_ref, payload: dict):
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO event_log (id, created_at, event, session_ref, payload) VALUES (?,?,?,?,?)",
            (new_id("e"), time.time(), event, session_ref, json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def insert_raw_query(text: str, ttl_days: int) -> str:
    qid = new_id("q")
    now = time.time()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO raw_queries (id, created_at, expires_at, query_text, consented) VALUES (?,?,?,?,1)",
            (qid, now, now + ttl_days * 86400, text),
        )
        conn.commit()
    finally:
        conn.close()
    return qid


def insert_feature_request(text_scrubbed: str, pii_kinds: list, ttl_days: int) -> str:
    """A demand-collection submission. Unlike raw_queries this is the point
    of the feature rather than a side effect of it, so it is stored by
    default -- but only after privacy.scrub() has run over it."""
    rid = new_id("fr")
    now = time.time()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO feature_requests (id, created_at, expires_at, text_scrubbed, pii_kinds) "
            "VALUES (?,?,?,?,?)",
            (rid, now, now + ttl_days * 86400, text_scrubbed,
             json.dumps(pii_kinds, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def count_feature_requests() -> int:
    conn = connect()
    try:
        return conn.execute("SELECT COUNT(*) c FROM feature_requests").fetchone()["c"]
    finally:
        conn.close()


def list_feature_requests(limit: int = 200) -> list:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, created_at, expires_at, text_scrubbed FROM feature_requests "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_all_feature_requests() -> int:
    conn = connect()
    try:
        c = conn.execute("DELETE FROM feature_requests").rowcount
        conn.commit()
        return c
    finally:
        conn.close()


def purge_expired(now: float = None) -> dict:
    now = now or time.time()
    cfg = get_config()
    cutoff = now - cfg.log_retention_days * 86400
    conn = connect()
    try:
        c1 = conn.execute("DELETE FROM raw_queries WHERE expires_at <= ?", (now,)).rowcount
        c2 = conn.execute("DELETE FROM event_log WHERE created_at <= ?", (cutoff,)).rowcount
        c3 = conn.execute("DELETE FROM feature_requests WHERE expires_at <= ?", (now,)).rowcount
        conn.commit()
        return {"raw_queries_deleted": c1, "events_deleted": c2,
                "feature_requests_deleted": c3}
    finally:
        conn.close()


def delete_all_raw_queries() -> int:
    conn = connect()
    try:
        c = conn.execute("DELETE FROM raw_queries").rowcount
        conn.commit()
        return c
    finally:
        conn.close()


def mutate_session(session_id, mutation):
    """Read, derive flags and write under one SQLite transaction, never over a model call."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            conn.rollback()
            return None
        fields = mutation(dict(row))
        if fields:
            fields["updated_at"] = time.time()
            if CONTENT_FIELDS.intersection(fields):
                fields["last_content_edited_at"] = fields["updated_at"]
            cols = ", ".join(f"{key} = ?" for key in fields)
            conn.execute(f"UPDATE sessions SET {cols} WHERE id = ?", (*fields.values(), session_id))
        updated = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
