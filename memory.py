"""
Long-term memory for the AI Secretary.

Three layers:
  1. event_log     — every email/event/briefing ever seen
  2. contacts      — per-person state: last contact, priority, open threads
  3. preferences   — user-defined rules and learned patterns

All stored in a local SQLite file: secretary_memory.db
"""

import sqlite3
import json
import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "secretary_memory.db"


# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,          -- 'email_seen' | 'briefing_sent' | 'calendar_event'
    source_id   TEXT,                   -- gmail message id or calendar event id
    summary     TEXT,                   -- short human-readable description
    metadata    TEXT,                   -- JSON blob for extra fields
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email               TEXT UNIQUE NOT NULL,
    name                TEXT,
    priority            TEXT DEFAULT 'normal',  -- 'high' | 'normal' | 'low'
    last_email_from     TEXT,                   -- ISO datetime: last time THEY emailed us
    last_email_to       TEXT,                   -- ISO datetime: last time WE emailed them
    open_thread         TEXT,                   -- short description of pending action, if any
    notes               TEXT,                   -- freeform notes about this person
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,          -- JSON-encoded value
    description TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS briefing_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT UNIQUE NOT NULL,   -- YYYY-MM-DD
    content     TEXT NOT NULL,
    email_count INTEGER,
    event_count INTEGER,
    created_at  TEXT NOT NULL
);
"""


# ── Connection ──────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ── Event log ───────────────────────────────────────────────────────────────────

def log_event(event_type: str, summary: str,
              source_id: str = None, metadata: dict = None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO event_log (event_type, source_id, summary, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_type, source_id, summary,
             json.dumps(metadata or {}),
             datetime.datetime.now(datetime.timezone.utc).isoformat())
        )


def already_seen_email(gmail_message_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM event_log WHERE event_type='email_seen' AND source_id=?",
            (gmail_message_id,)
        ).fetchone()
        return row is not None


# ── Contacts ────────────────────────────────────────────────────────────────────

def upsert_contact(email: str, name: str = None,
                   direction: str = None,    # 'from_them' | 'to_them'
                   open_thread: str = None):
    """Create or update a contact record when we see an email."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM contacts WHERE email=?", (email,)
        ).fetchone()

        if existing is None:
            conn.execute(
                "INSERT INTO contacts (email, name, last_email_from, last_email_to, "
                "open_thread, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (email, name,
                 now if direction == 'from_them' else None,
                 now if direction == 'to_them' else None,
                 open_thread, now)
            )
        else:
            updates = {"updated_at": now}
            if name and not existing["name"]:
                updates["name"] = name
            if direction == 'from_them':
                updates["last_email_from"] = now
            if direction == 'to_them':
                updates["last_email_to"] = now
            if open_thread is not None:
                updates["open_thread"] = open_thread

            set_clause = ", ".join(f"{k}=?" for k in updates)
            conn.execute(
                f"UPDATE contacts SET {set_clause} WHERE email=?",
                (*updates.values(), email)
            )


def get_overdue_contacts(days: int = 5) -> list[dict]:
    """People who emailed us but we haven't replied to in `days` days."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT email, name, last_email_from, last_email_to, open_thread, priority
            FROM contacts
            WHERE last_email_from IS NOT NULL
              AND last_email_from < ?
              AND (last_email_to IS NULL OR last_email_to < last_email_from)
            ORDER BY
              CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
              last_email_from ASC
            """,
            (cutoff,)
        ).fetchall()
        return [dict(r) for r in rows]


def set_contact_priority(email: str, priority: str, notes: str = None):
    """Manually mark a contact as high/normal/low priority."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE contacts SET priority=?, notes=COALESCE(?, notes), updated_at=? WHERE email=?",
            (priority, notes, now, email)
        )


def get_contact(email: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None


def list_high_priority_contacts() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM contacts WHERE priority='high' ORDER BY last_email_from DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Preferences ─────────────────────────────────────────────────────────────────

DEFAULT_PREFERENCES = {
    "briefing_time":          "08:00",
    "followup_after_days":    5,
    "max_emails_in_briefing": 5,
    "ignored_senders":        [],        # list of email patterns to skip
    "focus_areas":            [],        # e.g. ["sales", "engineering"] — colours the briefing
    "briefing_tone":          "warm",    # 'warm' | 'concise' | 'detailed'
}

def get_preference(key: str):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
        if row:
            return json.loads(row["value"])
        return DEFAULT_PREFERENCES.get(key)


def set_preference(key: str, value, description: str = None):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO preferences (key, value, description, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), description, now)
        )


def init_default_preferences():
    """Seed defaults on first run."""
    for key, value in DEFAULT_PREFERENCES.items():
        if get_preference(key) is None:
            set_preference(key, value)


# ── Briefing log ────────────────────────────────────────────────────────────────

def save_briefing(content: str, email_count: int, event_count: int):
    today = datetime.date.today().isoformat()
    now   = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO briefing_log (date, content, email_count, event_count, created_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(date) DO UPDATE SET content=excluded.content",
            (today, content, email_count, event_count, now)
        )


def get_recent_briefings(days: int = 3) -> list[dict]:
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, content FROM briefing_log WHERE date >= ? ORDER BY date DESC",
            (cutoff,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Memory summary for Claude ────────────────────────────────────────────────────

def build_memory_context() -> str:
    """
    Assemble a compact memory block to inject into Claude's prompt.
    Tells the agent what it already knows before it reads today's emails.
    """
    lines = []

    # Overdue follow-ups
    followup_days = get_preference("followup_after_days") or 5
    overdue = get_overdue_contacts(days=followup_days)
    if overdue:
        lines.append(f"⚠️  OVERDUE FOLLOW-UPS (no reply in {followup_days}+ days):")
        for c in overdue[:5]:
            name  = c["name"] or c["email"]
            since = c["last_email_from"][:10] if c["last_email_from"] else "unknown"
            thread = f" — {c['open_thread']}" if c["open_thread"] else ""
            tag   = " [HIGH PRIORITY]" if c["priority"] == "high" else ""
            lines.append(f"  • {name} ({c['email']}) — last heard from {since}{thread}{tag}")
        lines.append("")

    # High priority contacts
    vips = list_high_priority_contacts()
    if vips:
        lines.append("⭐  HIGH PRIORITY CONTACTS (always flag their emails):")
        for c in vips[:5]:
            lines.append(f"  • {c['name'] or c['email']} ({c['email']})")
        lines.append("")

    # Ignored senders
    ignored = get_preference("ignored_senders") or []
    if ignored:
        lines.append(f"🔇  IGNORED SENDERS (skip these): {', '.join(ignored)}")
        lines.append("")

    # User preferences
    tone    = get_preference("briefing_tone") or "warm"
    focus   = get_preference("focus_areas") or []
    lines.append(f"📋  BRIEFING STYLE: {tone}" +
                 (f" | Focus areas: {', '.join(focus)}" if focus else ""))

    # Recent context (yesterday's briefing summary — one line)
    recent = get_recent_briefings(days=1)
    if recent:
        first_line = recent[0]["content"].split("\n")[0]
        lines.append(f"📅  YESTERDAY: {first_line}")

    return "\n".join(lines) if lines else ""
