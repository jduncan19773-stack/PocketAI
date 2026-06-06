"""
memory.py — Session and long-term memory management (SQLite).

Tables:
  sessions  — conversation metadata (id, title, summary)
  messages  — every message in every conversation
  memories  — explicit facts the user wants the AI to always know

On each new query the system prompt is built from:
  [base instructions] + [user memories] + [last 3 session summaries]
"""

import time
import aiosqlite
from server.config import SESSIONS_DB, BASE_SYSTEM_PROMPT

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    title      TEXT DEFAULT 'New conversation',
    summary    TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sess_updated ON sessions(updated_at DESC);
"""


async def init_memory():
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def create_session(session_id: str, title: str = "New conversation"):
    now = time.time()
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO sessions (id, title, summary, created_at, updated_at) "
            "VALUES (?, ?, '', ?, ?)",
            (session_id, title, now, now)
        )
        await db.commit()


async def add_message(session_id: str, role: str, content: str):
    now = time.time()
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now)
        )
        await db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        await db.commit()


async def get_session_messages(session_id: str) -> list:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        db.row_factory = aiosqlite.Row
        cur  = await db.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
            (session_id,)
        )
        rows = await cur.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]


async def get_recent_sessions(limit: int = 30) -> list:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        db.row_factory = aiosqlite.Row
        cur  = await db.execute(
            "SELECT id, title, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        )
        rows = await cur.fetchall()
        return [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"]} for r in rows]


async def get_memories() -> list:
    """Return list of {id, content} dicts."""
    async with aiosqlite.connect(SESSIONS_DB) as db:
        db.row_factory = aiosqlite.Row
        cur  = await db.execute("SELECT id, content FROM memories ORDER BY created_at DESC")
        rows = await cur.fetchall()
        return [{"id": r["id"], "content": r["content"]} for r in rows]


async def save_memory(content: str):
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO memories (content, created_at) VALUES (?, ?)",
            (content.strip(), time.time())
        )
        await db.commit()


async def delete_memory(memory_id: int):
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        await db.commit()


async def get_recent_summaries(limit: int = 3) -> list:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        db.row_factory = aiosqlite.Row
        cur  = await db.execute(
            "SELECT summary FROM sessions WHERE summary != '' "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        )
        rows = await cur.fetchall()
        return [r["summary"] for r in rows]


async def update_session_title(session_id: str, title: str):
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
        await db.commit()


async def update_session_summary(session_id: str, summary: str):
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute("UPDATE sessions SET summary = ? WHERE id = ?", (summary, session_id))
        await db.commit()


async def build_system_prompt(session_id: str = None) -> str:
    """
    Compose the full system prompt:
      base instructions
      + persistent user memories (facts the user saved)
      + rolling history of prior conversations (loaded from the USB store)
      + recent session summaries
    """
    parts = [BASE_SYSTEM_PROMPT]

    memories = await get_memories()
    if memories:
        parts.append("\n\nThings to always remember about the user:\n" +
                     "\n".join(f"- {m['content']}" for m in memories))

    # Persistent cross-session history (survives restarts, stored on the USB).
    # Loaded newest-first within a token budget so the context never overflows.
    try:
        from server import history as history_mod
        prior = history_mod.load_recent_context()
        if prior:
            parts.append(
                "\n\nHere is what you and the user discussed in earlier "
                "conversations (use it as background context, and stay "
                "consistent with it):\n" + prior
            )
    except Exception:
        pass   # history is best-effort; never block a chat on it

    summaries = await get_recent_summaries()
    if summaries:
        parts.append("\n\nShort summaries of recent sessions:\n" +
                     "\n\n".join(summaries))

    return "".join(parts)
