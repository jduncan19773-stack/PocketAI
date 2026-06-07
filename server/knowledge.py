"""
knowledge.py — Offline knowledge base with BM25 retrieval (RAG).

PocketAI reads a local corpus of documents (Wikipedia / Wikinews extracts,
or anything you drop in) and, on every question, retrieves the most relevant
passages and feeds them to the model as context. This lets it answer about
topics and recent events that aren't in the model's training data — fully
offline, no internet, no embedding model required.

Retrieval engine: SQLite FTS5 (BM25 ranking).
  - Scales to gigabytes of text with tiny memory (streams from disk)
  - No embedding model to bundle, no extra server process
  - Strong for factual / keyword queries (news, reference lookups)

Layout (on the USB drive / project root):
  knowledge/
    seed/        — small starter corpus, committed to git (works on fresh clone)
    corpus/      — bulk corpus built locally by build_knowledge.py (gitignored)
    index.db     — FTS5 search index (gitignored, rebuilt from the above)

Each corpus file is plain text. The first lines may be a small header:
  TITLE: ...
  SOURCE: ...
  URL: ...
  (blank line, then the body)
"""

import json
import os
import re
import sqlite3
from pathlib import Path

_HERE          = Path(__file__).resolve().parent.parent   # project root / USB root
KNOWLEDGE_DIR  = _HERE / "knowledge"
SEED_DIR       = KNOWLEDGE_DIR / "seed"
CORPUS_DIR     = KNOWLEDGE_DIR / "corpus"
INDEX_DB       = KNOWLEDGE_DIR / "index.db"
INDEX_META     = KNOWLEDGE_DIR / "index_meta.json"   # cached counts (instant stats)

# Passage chunking — ~300 tokens per chunk with a little overlap
CHUNK_CHARS    = 1200
CHUNK_OVERLAP  = 150

# Separator between articles inside a "shard" file. The HF bulk builder packs
# many articles into one file (to avoid hundreds of thousands of tiny files on
# the USB); single-article files from the API crawler simply have no separator.
DOC_SEP = "\n<<<<POCKETAI_DOC>>>>\n"

# How many passages to retrieve per query, and how much text to inject.
# Kept deliberately small: USB mode runs CPU-only inference, where every extra
# token of injected context slows prompt-processing a lot. The single best
# passage (plus one backup) is usually enough for a good grounded answer.
TOP_K          = 2
MAX_CONTEXT_CHARS = 2000

_STOPWORDS = {
    "the","a","an","is","are","was","were","be","been","to","of","and","or",
    "in","on","at","by","for","with","as","that","this","it","its","what",
    "who","when","where","why","how","do","does","did","i","you","me","my",
    "tell","about","give","please","can","could","would","explain",
}


# ── Setup ────────────────────────────────────────────────────────

def init_knowledge() -> None:
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(str(INDEX_DB))


def _iter_corpus_files():
    """Yield all corpus text files (committed seed + locally-built bulk)."""
    for d in (SEED_DIR, CORPUS_DIR):
        if d.exists():
            yield from sorted(d.glob("*.txt"))


def _parse_segment(seg: str, default_title: str) -> tuple[str, str, str]:
    """Parse one article segment (optional TITLE/SOURCE/URL header + body)."""
    title, source, body = default_title, "", seg
    if seg.startswith("TITLE:"):
        head, _, rest = seg.partition("\n\n")
        for line in head.splitlines():
            if line.startswith("TITLE:"):
                title = line[6:].strip()
            elif line.startswith("SOURCE:"):
                source = line[7:].strip()
            elif line.startswith("URL:"):
                source = source or line[4:].strip()
        body = rest
    return title, source, body


def _iter_docs(path: Path):
    """
    Yield (title, source, body) for every article in a corpus file.
    Most files hold one article; HF shard files hold many (split on DOC_SEP).
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return
    if DOC_SEP in raw:
        for seg in raw.split(DOC_SEP):
            seg = seg.strip()
            if seg:
                yield _parse_segment(seg, path.stem)
    else:
        yield _parse_segment(raw, path.stem)


def _chunk(text: str):
    """Split body text into overlapping passages."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return
    i, n = 0, len(text)
    while i < n:
        end = min(i + CHUNK_CHARS, n)
        # Try to break on a paragraph/sentence boundary near the end
        if end < n:
            br = text.rfind("\n", i + CHUNK_CHARS // 2, end)
            if br == -1:
                br = text.rfind(". ", i + CHUNK_CHARS // 2, end)
            if br != -1:
                end = br + 1
        chunk = text[i:end].strip()
        if chunk:
            yield chunk
        if end >= n:
            break
        i = max(end - CHUNK_OVERLAP, i + 1)


# ── Index building ───────────────────────────────────────────────

def build_index() -> dict:
    """
    (Re)build the FTS5 index from every corpus file. Returns stats.
    Safe to call repeatedly; it rebuilds from scratch.
    """
    init_knowledge()
    if INDEX_DB.exists():
        try:
            INDEX_DB.unlink()
        except Exception:
            pass

    conn = _connect()
    try:
        # detail='none' drops per-term position data we don't use (we only do
        # term-match + BM25 ranking, not phrase/NEAR search). This roughly
        # halves the on-disk index, important for fitting a big corpus on a USB.
        conn.execute(
            "CREATE VIRTUAL TABLE chunks USING fts5("
            "title, source, content, tokenize='porter unicode61', detail='none')"
        )
        docs = 0
        passages = 0
        batch = []
        for path in _iter_corpus_files():
            for title, source, body in _iter_docs(path):
                had = False
                for ch in _chunk(body):
                    batch.append((title, source, ch))
                    passages += 1
                    had = True
                    if len(batch) >= 1000:
                        conn.executemany(
                            "INSERT INTO chunks (title, source, content) VALUES (?,?,?)", batch
                        )
                        batch.clear()
                if had:
                    docs += 1
        if batch:
            conn.executemany(
                "INSERT INTO chunks (title, source, content) VALUES (?,?,?)", batch
            )
        conn.commit()
        # Cache counts so kb_stats() never has to COUNT(*) a multi-GB index
        files = list(_iter_corpus_files())
        size_mb = round(sum((f.stat().st_size for f in files), 0) / (1024 * 1024), 1)
        try:
            INDEX_META.write_text(json.dumps(
                {"documents": docs, "passages": passages, "size_mb": size_mb}
            ), encoding="utf-8")
        except Exception:
            pass
        global _stats_cache
        _stats_cache = None   # force re-read next stats call
        return {"documents": docs, "passages": passages}
    finally:
        conn.close()


def ensure_index() -> None:
    """Build the index on startup if it does not exist yet."""
    init_knowledge()
    if INDEX_DB.exists():
        return
    # Only build if there is any corpus to index
    if any(_iter_corpus_files()):
        build_index()


def prewarm_index() -> None:
    """
    Read the index file sequentially to pull it into the OS page cache.
    Retrieval over a multi-GB index on a USB drive is slow on the FIRST read
    of each query's postings (cold cache), then instant once cached. Warming
    the whole index up front makes every query fast and consistent.

    Call this from a background thread on startup; it just does sequential
    I/O and exits. Safe if the file is missing.
    """
    if not INDEX_DB.exists():
        return
    try:
        chunk = 8 * 1024 * 1024   # 8 MB reads
        with open(INDEX_DB, "rb", buffering=0) as f:
            while True:
                if not f.read(chunk):
                    break
    except Exception:
        pass


# ── Retrieval ────────────────────────────────────────────────────

def _match_query(query: str) -> str:
    """
    Turn a natural-language question into a safe FTS5 MATCH expression:
    keep meaningful words, drop stopwords/punctuation, OR them together.
    Returns "" if there is nothing useful to search for.
    """
    words = re.findall(r"[A-Za-z0-9]+", query.lower())
    # Keep meaningful terms: drop stopwords, keep words of 2+ chars, and always
    # keep any token containing a digit (years, "15", model names, etc.).
    terms = [
        w for w in words
        if w not in _STOPWORDS and (len(w) >= 2 or any(ch.isdigit() for ch in w))
    ]
    if not terms:
        return []
    # De-duplicate, preserve order
    seen = []
    for t in terms:
        if t not in seen:
            seen.append(t)
    return seen[:12]


def _run_match(conn, match: str, k: int) -> list:
    try:
        cur = conn.execute(
            "SELECT title, source, content, bm25(chunks, 10.0, 1.0, 1.0) AS score "
            "FROM chunks WHERE chunks MATCH ? ORDER BY score LIMIT ?",
            (match, k),
        )
        return cur.fetchall()
    except sqlite3.OperationalError:
        return []


def search(query: str, k: int = TOP_K) -> list[dict]:
    """
    Return up to k best-matching passages for a query.

    Strategy: try an AND query first (all terms must appear). FTS5 intersects
    the postings lists starting from the rarest term, so AND is typically
    30-260x faster than OR on a large USB index AND more precise. If AND finds
    nothing (no single passage holds every term), fall back to OR for recall.
    """
    if not INDEX_DB.exists():
        return []
    terms = _match_query(query)
    if not terms:
        return []

    conn = _connect()
    try:
        # Fast, precise path: require all terms
        and_match = " AND ".join(f'"{t}"' for t in terms)
        rows = _run_match(conn, and_match, k)

        # Recall fallback: any term (only runs when AND found nothing)
        if not rows:
            or_match = " OR ".join(f'"{t}"' for t in terms)
            rows = _run_match(conn, or_match, k)
    finally:
        conn.close()
    return [{"title": r[0], "source": r[1], "content": r[2], "score": r[3]} for r in rows]


def search_context(query: str, k: int = TOP_K) -> str:
    """
    Build a context block of relevant knowledge for a query, or "" if none.
    This is what gets injected into the model's prompt.
    """
    hits = search(query, k)
    if not hits:
        return ""
    parts = []
    used = 0
    for h in hits:
        snippet = h["content"].strip()
        label = h["title"] or "source"
        block = f"[{label}] {snippet}"
        if used + len(block) > MAX_CONTEXT_CHARS and parts:
            break
        parts.append(block)
        used += len(block)
    if not parts:
        return ""
    return (
        "Relevant information from your offline knowledge base "
        "(use it to answer if helpful, and don't invent details beyond it):\n\n"
        + "\n\n".join(parts)
    )


# kb_stats is polled by the UI; the passage COUNT(*) over a multi-GB FTS index
# on USB is slow, so compute it once and cache it (the corpus is static at runtime).
_stats_cache: dict | None = None

def kb_stats() -> dict:
    """
    Stats for /api/status — always fast. Reads cached counts from a small
    metadata file written at build time; never runs COUNT(*) on the USB index.
    """
    global _stats_cache
    if _stats_cache is not None:
        return _stats_cache

    meta = {"documents": 0, "passages": 0, "size_mb": 0.0}
    if INDEX_META.exists():
        try:
            meta = json.loads(INDEX_META.read_text(encoding="utf-8"))
        except Exception:
            pass

    _stats_cache = {**meta, "indexed": INDEX_DB.exists()}
    return _stats_cache
