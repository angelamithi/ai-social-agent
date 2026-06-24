"""
Postgres-backed storage, shared between the cron job (main.py) and the
always-on web service (webhook_server.py) via Render's managed Postgres.

This replaces the old local-JSON-file storage. On Render, the cron job
and web service run in separate containers with separate filesystems —
they cannot share local files — so a real database is required for
both to see the same pending-approval state and post history.

Images are stored as bytes directly in Postgres (not on a local disk)
for the same reason: a disk attached to one service isn't reachable
from the other. The web service serves them back out over HTTP from
the `images` table.

Connects via the DATABASE_URL environment variable, which Render
injects automatically when a Postgres instance is linked to a service
(or which you set manually in .env for local testing against any
Postgres instance).
"""

import hashlib
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    platform TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_url TEXT,
    status TEXT NOT NULL,
    preview TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_platform_timestamp ON history (platform, timestamp);
CREATE INDEX IF NOT EXISTS idx_history_content_hash ON history (content_hash);

CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    data BYTEA NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'image/png',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pending_approval (
    id INTEGER PRIMARY KEY DEFAULT 1,
    approval_id TEXT,
    status TEXT,
    created_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    item_title TEXT,
    source_url TEXT,
    x_text TEXT,
    linkedin_text TEXT,
    facebook_text TEXT,
    image_id TEXT,
    whatsapp_message_id TEXT,
    CONSTRAINT single_row CHECK (id = 1)
);
"""


@contextmanager
def _connection():
    # Render's managed Postgres requires SSL; a local Postgres for
    # development usually doesn't have it configured. DATABASE_SSLMODE
    # lets local testing set sslmode=disable without touching
    # production behavior (which defaults to "require").
    sslmode = config.DATABASE_SSLMODE
    conn = psycopg2.connect(config.DATABASE_URL, sslmode=sslmode)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    """Create tables if they don't exist yet. Safe to call on every
    process start — CREATE TABLE IF NOT EXISTS is idempotent."""
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)


# ---------------------------------------------------------------------------
# History / dedup / rate limiting (replaces old storage.py)
# ---------------------------------------------------------------------------

def content_hash(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()


def is_duplicate(text: str, source_url: str = None) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.DEDUP_LOOKBACK_DAYS)
    h = content_hash(text)

    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM history
                WHERE timestamp >= %s
                  AND (content_hash = %s OR (%s::text IS NOT NULL AND source_url = %s))
                LIMIT 1
                """,
                (cutoff, h, source_url, source_url),
            )
            return cur.fetchone() is not None


def record_post(platform: str, text: str, source_url: str = None, status: str = "posted") -> None:
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO history (timestamp, platform, content_hash, source_url, status, preview)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (datetime.now(timezone.utc), platform, content_hash(text), source_url, status, text[:120]),
            )


def posts_today(platform: str) -> int:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM history
                WHERE platform = %s AND status = 'posted' AND timestamp >= %s
                """,
                (platform, today_start),
            )
            return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Image storage (replaces local generated_images/ directory)
# ---------------------------------------------------------------------------

def save_image(image_bytes: bytes, content_type: str = "image/png") -> str:
    """Store image bytes in Postgres. Returns a generated image_id used
    to build the public URL the WhatsApp template points to."""
    image_id = uuid.uuid4().hex
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO images (id, data, content_type) VALUES (%s, %s, %s)",
                (image_id, psycopg2.Binary(image_bytes), content_type),
            )
    return image_id


def get_image(image_id: str) -> dict:
    """Returns {"data": bytes, "content_type": str} or None if not found."""
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data, content_type FROM images WHERE id = %s", (image_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return {"data": bytes(row[0]), "content_type": row[1]}


# ---------------------------------------------------------------------------
# Pending approval (replaces approval_queue.py's local JSON file)
# ---------------------------------------------------------------------------
# Single-row table (id always 1) — matches the "one pending item at a
# time" design. Upserting into this row is how a new pending approval
# is created; resolving it just updates the status in place.

def has_pending() -> bool:
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM pending_approval WHERE id = 1")
            row = cur.fetchone()
            return row is not None and row[0] == "pending"


def create_pending(item: dict, x_text: str, linkedin_text: str, facebook_text: str,
                    image_id: str, source_url: str = None) -> str:
    approval_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pending_approval
                    (id, approval_id, status, created_at, resolved_at, item_title,
                     source_url, x_text, linkedin_text, facebook_text, image_id, whatsapp_message_id)
                VALUES (1, %s, 'pending', %s, NULL, %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (id) DO UPDATE SET
                    approval_id = EXCLUDED.approval_id,
                    status = EXCLUDED.status,
                    created_at = EXCLUDED.created_at,
                    resolved_at = NULL,
                    item_title = EXCLUDED.item_title,
                    source_url = EXCLUDED.source_url,
                    x_text = EXCLUDED.x_text,
                    linkedin_text = EXCLUDED.linkedin_text,
                    facebook_text = EXCLUDED.facebook_text,
                    image_id = EXCLUDED.image_id,
                    whatsapp_message_id = NULL
                """,
                (approval_id, now, item.get("title", ""), source_url,
                 x_text, linkedin_text, facebook_text, image_id),
            )
    return approval_id


def _row_to_dict(row) -> dict:
    if row is None:
        return None
    columns = ["approval_id", "status", "created_at", "resolved_at", "item_title",
               "source_url", "x_text", "linkedin_text", "facebook_text", "image_id",
               "whatsapp_message_id"]
    return dict(zip(columns, row))


def get_pending() -> dict:
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT approval_id, status, created_at, resolved_at, item_title,
                          source_url, x_text, linkedin_text, facebook_text, image_id,
                          whatsapp_message_id
                   FROM pending_approval WHERE id = 1 AND status = 'pending'"""
            )
            return _row_to_dict(cur.fetchone())


def get_by_id(approval_id: str) -> dict:
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT approval_id, status, created_at, resolved_at, item_title,
                          source_url, x_text, linkedin_text, facebook_text, image_id,
                          whatsapp_message_id
                   FROM pending_approval WHERE id = 1 AND approval_id = %s""",
                (approval_id,),
            )
            return _row_to_dict(cur.fetchone())


def resolve(approval_id: str, decision: str) -> dict:
    """Mark the pending entry as 'approve'/'reject'/etc. Returns the
    resolved entry, or None if approval_id didn't match the current
    pending entry (e.g. a stale/duplicate webhook delivery)."""
    now = datetime.now(timezone.utc)
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pending_approval
                SET status = %s, resolved_at = %s
                WHERE id = 1 AND approval_id = %s AND status = 'pending'
                RETURNING approval_id, status, created_at, resolved_at, item_title,
                          source_url, x_text, linkedin_text, facebook_text, image_id,
                          whatsapp_message_id
                """,
                (decision, now, approval_id),
            )
            return _row_to_dict(cur.fetchone())


def set_whatsapp_message_id(approval_id: str, message_id: str) -> None:
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pending_approval SET whatsapp_message_id = %s WHERE id = 1 AND approval_id = %s",
                (message_id, approval_id),
            )
