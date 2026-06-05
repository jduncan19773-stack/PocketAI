"""
cache.py — Two-level query cache backed by SQLite.

Level 1: Exact match on normalised query hash (instant, O(1)).
Level 2: Fuzzy word-overlap match for near-duplicate queries (~O(n)).

All cache data is stored in DATA_DIR/cache.db so it travels on the USB.
"""

import hashlib
import re
import time
import aiosqlite
from server.config import CACHE_DB

FUZZY_THRESHOLD = 0.82

_STOPWORDS = {
    "a","an","the","is","are","was","were","be","been","being",
    "have","has","had","do","does","did","will","would","could",
    "should","may","might","shall","can","to","of","in","on","at",
    "by","for","with","about","from","and","but","or","not","only",
    "own","same","than","too","very","just","what","which","who",
    "this","that","these","those","i","me","my","we","our",
    "you","your","he","him","his","she","her","it","its","they","them",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    hash        TEXT PRIMARY KEY,
    query       TEXT NOT NULL,
    keywords    TEXT NOT NULL,
    response    TEXT NOT NULL,
    model_a     TEXT NOT NULL,
    model_b     TEXT NOT NULL,
    hits        INTEGER DEFAULT 0,
    created_at  REAL NOT NULL,
    last_hit    REAL
);
CREATE INDEX IF NOT EXISTS idx_cache_created ON cache(created_at);
"""


def _normalise(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text)


def _keywords(text: str) -> set:
    return {w for w in _normalise(text).split() if w not in _STOPWORDS and len(w) > 2}


def _hash(query: str, model_a: str, model_b: str) -> str:
    key = f"{model_a}:{model_b}:{_normalise(query)}"
    return hashlib.sha256(key.encode()).hexdigest()


def _overlap(set_a: set, set_b: set) -> float:
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


async def init_cache():
    async with aiosqlite.connect(CACHE_DB) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def get_cached(query: str, model_a: str, model_b: str):
    exact_hash = _hash(query, model_a, model_b)
    query_kw   = _keywords(query)
    now        = time.time()

    async with aiosqlite.connect(CACHE_DB) as db:
        db.row_factory = aiosqlite.Row

        # Level 1: exact match
        cur = await db.execute("SELECT hash, response FROM cache WHERE hash = ?", (exact_hash,))
        row = await cur.fetchone()
        if row:
            await db.execute(
                "UPDATE cache SET hits = hits + 1, last_hit = ? WHERE hash = ?",
                (now, exact_hash)
            )
            await db.commit()
            return row["response"]

        # Level 2: fuzzy — scan last 2000 entries
        cur = await db.execute(
            "SELECT hash, keywords, response FROM cache "
            "WHERE model_a = ? AND model_b = ? "
            "ORDER BY last_hit DESC, created_at DESC LIMIT 2000",
            (model_a, model_b)
        )
        rows = await cur.fetchall()
        for r in rows:
            cached_kw = set(r["keywords"].split(",")) if r["keywords"] else set()
            if _overlap(query_kw, cached_kw) >= FUZZY_THRESHOLD:
                await db.execute(
                    "UPDATE cache SET hits = hits + 1, last_hit = ? WHERE hash = ?",
                    (now, r["hash"])
                )
                await db.commit()
                return r["response"]

    return None


async def save_cache(query: str, model_a: str, model_b: str, response: str):
    h      = _hash(query, model_a, model_b)
    kw_str = ",".join(_keywords(query))
    now    = time.time()
    async with aiosqlite.connect(CACHE_DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO cache "
            "(hash, query, keywords, response, model_a, model_b, hits, created_at, last_hit) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (h, query, kw_str, response, model_a, model_b, now, now)
        )
        await db.commit()


async def cache_stats() -> dict:
    async with aiosqlite.connect(CACHE_DB) as db:
        db.row_factory = aiosqlite.Row
        cur  = await db.execute("SELECT COUNT(*) as total, SUM(hits) as total_hits FROM cache")
        row  = await cur.fetchone()
        return {
            "total_entries": row["total"]      or 0,
            "total_hits":    row["total_hits"] or 0,
        }
