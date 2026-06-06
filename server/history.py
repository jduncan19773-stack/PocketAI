"""
history.py — Persistent conversation memory across restarts.

Every exchange (user message + AI reply) is appended to a rolling history
store on the flash drive. On startup the most recent history is loaded and
fed into the system prompt, so PocketAI "remembers" prior conversations
even after the app is closed and reopened.

Disk management:
  - Lives in DATA_DIR/history/ on the USB (one JSON file per exchange).
  - Uses whatever free space the drive has, always keeping MIN_FREE_MB spare.
  - When the drive gets low, the OLDEST history files are deleted first
    (FIFO) until there is room again — newest conversations always win.

Context budget:
  - On each query the most recent exchanges are loaded newest-first until a
    token budget is reached, so the model's context window is never overflowed.
"""

import json
import shutil
import time
from pathlib import Path

from server.config import DATA_DIR

# ── Configuration ────────────────────────────────────────────────

HISTORY_DIR = Path(DATA_DIR) / "history"

# Always leave at least this much free space on the drive (megabytes)
MIN_FREE_MB = 500

# How many characters of history to feed into the prompt (≈ 4 chars/token).
# 40k chars ≈ 10k tokens, comfortably inside the 16k context window while
# leaving room for the current question and the answer.
CONTEXT_CHAR_BUDGET = 40_000

# Safety cap so a single giant exchange can't dominate the budget
MAX_EXCHANGE_CHARS = 6_000


def init_history() -> None:
    """Create the history folder if it does not exist."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _free_mb() -> float:
    """Return free space on the history drive in megabytes."""
    try:
        usage = shutil.disk_usage(str(HISTORY_DIR))
        return usage.free / (1024 * 1024)
    except Exception:
        return float("inf")   # if we can't tell, don't block writes


def _history_files() -> list[Path]:
    """All history files, OLDEST first (sorted by filename timestamp)."""
    return sorted(HISTORY_DIR.glob("*.json"))


def prune_for_space(min_free_mb: int = MIN_FREE_MB) -> int:
    """
    Delete oldest history files until at least `min_free_mb` is free.
    Returns the number of files removed.
    """
    removed = 0
    files = _history_files()
    # Delete oldest-first while we're under the free-space floor and files remain
    while _free_mb() < min_free_mb and files:
        oldest = files.pop(0)
        try:
            oldest.unlink()
            removed += 1
        except Exception:
            break
    return removed


def save_exchange(session_id: str, user_msg: str, ai_msg: str) -> None:
    """
    Append one completed exchange to the history store.
    Makes room first if the drive is low on space (oldest deleted first).
    """
    init_history()

    # Make room before writing (keep MIN_FREE_MB spare)
    prune_for_space()

    # Trim very long messages so one exchange can't blow the budget
    record = {
        "ts":        time.time(),
        "session":   session_id,
        "user":      user_msg[:MAX_EXCHANGE_CHARS],
        "assistant": ai_msg[:MAX_EXCHANGE_CHARS],
    }

    # Filename = millisecond timestamp so lexical sort == chronological sort
    fname = f"{int(record['ts'] * 1000):020d}.json"
    try:
        (HISTORY_DIR / fname).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        # Drive full despite pruning — drop oldest and try once more
        prune_for_space(min_free_mb=MIN_FREE_MB + 50)
        try:
            (HISTORY_DIR / fname).write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass   # give up silently; never crash a chat over history I/O


def load_recent_context(char_budget: int = CONTEXT_CHAR_BUDGET) -> str:
    """
    Build a context block from the most recent exchanges, newest-first,
    until the character budget is reached. Returns "" if there is no history.

    The block is ordered oldest→newest in the final text so the model reads
    it in natural chronological order.
    """
    init_history()
    files = _history_files()
    if not files:
        return ""

    chosen: list[dict] = []
    used = 0
    # Walk newest-first, collecting until the budget is spent
    for path in reversed(files):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        chunk = len(rec.get("user", "")) + len(rec.get("assistant", ""))
        if used + chunk > char_budget and chosen:
            break
        chosen.append(rec)
        used += chunk

    if not chosen:
        return ""

    # Reverse back to chronological order for the prompt
    chosen.reverse()
    lines = []
    for rec in chosen:
        u = rec.get("user", "").strip()
        a = rec.get("assistant", "").strip()
        if u:
            lines.append(f"User: {u}")
        if a:
            lines.append(f"PocketAI: {a}")

    return "\n".join(lines)


def history_stats() -> dict:
    """Return basic stats for diagnostics."""
    files = _history_files()
    total_bytes = sum((f.stat().st_size for f in files), 0)
    return {
        "exchanges": len(files),
        "size_kb":   round(total_bytes / 1024, 1),
        "free_mb":   round(_free_mb(), 1),
    }
