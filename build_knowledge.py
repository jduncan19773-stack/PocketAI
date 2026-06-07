"""
build_knowledge.py — Fill PocketAI's offline knowledge base.

Pulls plain-text article extracts from Wikipedia and Wikinews (both have
official APIs and openly-licensed content) and saves them as text files in
the knowledge corpus. Then rebuilds the search index so PocketAI can use them.

It crawls breadth-first from a set of seed topics (recent events + major
reference subjects), following article links, until the target size is hit.

Two sources:
  --source api  (default)  Crawl Wikipedia + Wikinews live APIs. Reliable and
                           current (good for recent news), but SLOW — only
                           practical up to ~100-200 MB.
  --source hf              Stream Hugging Face's pre-cleaned Wikipedia dataset
                           (wikimedia/wikipedia). FAST — reaches multiple GB in
                           minutes. Best for bulk. Needs the 'datasets' library
                           on the build machine (build-time only):
                               pip install datasets

Usage:
  python build_knowledge.py                          # Simple English Wikipedia
                                                     # (~260MB, ~147k canonical
                                                     # articles) -- recommended
  python build_knowledge.py --hf-config 20231101.en --target-mb 3000
                                                     # full English (needs 32GB+ USB)
  python build_knowledge.py --source api --seed      # small committed seed set

Sources are openly licensed: Wikipedia CC BY-SA, Wikinews CC BY.
Re-runnable: existing files are skipped, so you can top up over time.
Provision a new flash drive by cloning the repo and running this script.
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

HERE        = Path(__file__).resolve().parent
KNOWLEDGE   = HERE / "knowledge"
SEED_DIR    = KNOWLEDGE / "seed"
CORPUS_DIR  = KNOWLEDGE / "corpus"

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKINEWS_API  = "https://en.wikinews.org/w/api.php"

USER_AGENT = "PocketAI-KnowledgeBuilder/1.0 (offline personal assistant; contact: local)"

# Seed topics — recent-events hubs plus broad reference anchors. The crawler
# expands outward from these by following article links.
SEED_TOPICS = [
    # Recent-events hubs
    "Portal:Current events", "2025", "2026", "2025 in the United States",
    "2025 in politics", "2025 in science", "Timeline of the 21st century",
    # World / reference anchors that branch widely
    "United States", "United Kingdom", "European Union", "China", "India",
    "Artificial intelligence", "Climate change", "World economy",
    "Technology", "Science", "History of the world", "Health", "Medicine",
    "Space exploration", "Renewable energy", "Internet", "Economics",
    "Geopolitics", "Elections", "Nobel Prize", "Olympic Games",
]


def _api_get(api: str, params: dict, retries: int = 3) -> dict:
    params = {**params, "format": "json"}
    url = api + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except Exception:
            if attempt == retries - 1:
                return {}
            time.sleep(1.5 * (attempt + 1))
    return {}


def _safe_filename(title: str, prefix: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")[:80] or "page"
    return f"{prefix}__{slug}.txt"


def fetch_extract_and_links(api: str, title: str) -> tuple[str, list[str]]:
    """Return (plain_text_extract, [linked_titles]) for an article."""
    data = _api_get(api, {
        "action": "query",
        "prop": "extracts|links",
        "explaintext": 1,
        "redirects": 1,
        "pllimit": 200,
        "plnamespace": 0,
        "titles": title,
    })
    pages = (data.get("query", {}) or {}).get("pages", {})
    for _, page in pages.items():
        extract = page.get("extract", "") or ""
        links = [l["title"] for l in page.get("links", []) if l.get("ns") == 0]
        return extract, links
    return "", []


def crawl(api: str, source_name: str, prefix: str, out_dir: Path,
          seeds: list[str], target_bytes: int, current_bytes: int,
          delay: float = 0.3) -> int:
    """
    Breadth-first crawl from seeds, saving extracts to out_dir until the
    target size is reached. Returns the new total byte count.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    queue: deque[str] = deque(seeds)
    total = current_bytes

    while queue and total < target_bytes:
        title = queue.popleft()
        if title in seen:
            continue
        seen.add(title)

        fname = out_dir / _safe_filename(title, prefix)
        if fname.exists():
            continue

        extract, links = fetch_extract_and_links(api, title)
        time.sleep(delay)   # be polite to the API

        # Skip stubs / disambiguation / empty
        if not extract or len(extract) < 600:
            # still harvest links so the frontier keeps growing
            for l in links[:40]:
                if l not in seen:
                    queue.append(l)
            continue

        header = f"TITLE: {title}\nSOURCE: {source_name}\nURL: {api.split('/w/')[0]}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}\n\n"
        try:
            fname.write_text(header + extract, encoding="utf-8")
            total += len(header) + len(extract.encode("utf-8", "ignore"))
        except Exception:
            continue

        # Expand frontier
        for l in links:
            if l not in seen:
                queue.append(l)

        mb = total / (1024 * 1024)
        print(f"\r  [{source_name}] {len(seen)} seen, {mb:.1f} MB saved — last: {title[:48]}", end="", flush=True)

    print()
    return total


def build_from_hf(out_dir: Path, target_bytes: int, hf_config: str) -> int:
    """
    Stream Hugging Face's cleaned Wikipedia dataset and write articles to
    shard files (many articles per file) until the target size is reached.
    Returns total bytes written. Requires the 'datasets' library.
    """
    try:
        from datasets import load_dataset
    except ImportError:
        print()
        print("  The Hugging Face fast-path needs the 'datasets' library.")
        print("  Install it on this machine (build-time only), then re-run:")
        print("      pip install datasets")
        print()
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sum((f.stat().st_size for f in out_dir.glob("hf_shard_*.txt")), 0)
    total = existing
    if existing:
        print(f"  Resuming — {existing/1024/1024:.1f} MB of HF shards already present.")

    print(f"  Streaming wikimedia/wikipedia ({hf_config})...")
    ds = load_dataset("wikimedia/wikipedia", hf_config, split="train", streaming=True)

    SHARD_BYTES = 10 * 1024 * 1024     # ~10 MB per shard file
    shard_idx = 1 + len(list(out_dir.glob("hf_shard_*.txt")))
    buf: list[str] = []
    buf_bytes = 0
    count = 0

    def flush_shard(idx, parts):
        path = out_dir / f"hf_shard_{idx:05d}.txt"
        path.write_text(DOC_SEP.join(parts), encoding="utf-8")

    from server.knowledge import DOC_SEP
    for row in ds:
        if total >= target_bytes:
            break
        title = (row.get("title") or "").strip()
        text  = (row.get("text") or "").strip()
        url   = row.get("url") or ""
        if len(text) < 400:
            continue
        block = f"TITLE: {title}\nSOURCE: Wikipedia\nURL: {url}\n\n{text}"
        b = len(block.encode("utf-8", "ignore"))
        buf.append(block)
        buf_bytes += b
        total += b
        count += 1
        if buf_bytes >= SHARD_BYTES:
            flush_shard(shard_idx, buf)
            shard_idx += 1
            buf.clear()
            buf_bytes = 0
            print(f"\r  {count:,} articles, {total/1024/1024:.0f} MB written — last: {title[:42]}", end="", flush=True)

    if buf:
        flush_shard(shard_idx, buf)
    print()
    print(f"  Wrote {count:,} articles ({total/1024/1024:.0f} MB) in shard files.")
    return total


def main():
    # Make all console/log output UTF-8 safe (Wikipedia titles have non-Latin
    # characters that crash the default Windows cp1252 console encoding).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Build PocketAI's knowledge base")
    ap.add_argument("--source", choices=["api", "hf"], default="hf",
                    help="hf = Hugging Face Wikipedia dump (fast, recommended); "
                         "api = live Wikipedia/Wikinews crawl (current events, slow)")
    ap.add_argument("--target-mb", type=int, default=2000,
                    help="Max corpus size in MB (default 2000; Simple Wikipedia is ~260MB)")
    ap.add_argument("--seed", action="store_true",
                    help="Write into knowledge/seed (the small committed set) instead of corpus/")
    ap.add_argument("--delay", type=float, default=0.3,
                    help="Seconds between API calls (api source only; default 0.3)")
    ap.add_argument("--hf-config", default="20231101.simple",
                    help="Hugging Face wikimedia/wikipedia config. Default 20231101.simple "
                         "(Simple English: ~147k canonical articles, ~260MB, best accuracy/size). "
                         "Use 20231101.en for full English (much larger, needs a 32GB+ drive).")
    args = ap.parse_args()

    out_dir = SEED_DIR if args.seed else CORPUS_DIR
    target_bytes = args.target_mb * 1024 * 1024

    print()
    print("  ============================================================")
    print("   PocketAI — Knowledge Base Builder")
    print("  ============================================================")
    print(f"  Target: ~{args.target_mb} MB into {out_dir}")
    print(f"  Source: {'Hugging Face Wikipedia dump' if args.source == 'hf' else 'Wikipedia + Wikinews live API'}")
    print()

    # ── Fast bulk path: Hugging Face dump ────────────────────────
    if args.source == "hf":
        build_from_hf(out_dir, target_bytes, args.hf_config)
        print("  Rebuilding search index...")
        sys.path.insert(0, str(HERE))
        from server import knowledge
        stats = knowledge.build_index()
        print(f"  Indexed {stats['documents']} documents into {stats['passages']} passages.")
        print()
        print("  Done. PocketAI will use this knowledge base on its next launch.")
        print()
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    # Count what's already there (re-runnable / resumable)
    existing = sum((f.stat().st_size for f in out_dir.glob("*.txt")), 0)
    if existing:
        print(f"  Resuming — {existing/1024/1024:.1f} MB already present.")
    total = existing

    # 1) Recent news from Wikinews first (genuinely recent, but smaller) —
    #    capped to ~20% of the target so reference content dominates.
    try:
        news_titles = _recent_wikinews_titles(300)
        if news_titles:
            news_budget = existing + max(int(target_bytes * 0.20), 1)
            news_budget = min(news_budget, target_bytes)
            print(f"  Pulling recent news from Wikinews ({len(news_titles)} recent articles)...")
            total = crawl(WIKINEWS_API, "Wikinews", "wikinews", out_dir,
                          news_titles, news_budget, total, args.delay)
    except Exception as e:
        print(f"  (Wikinews step skipped: {e})")

    # 2) Fill the rest from Wikipedia via breadth-first crawl from seed topics.
    print(f"  Pulling reference articles from Wikipedia...")
    total = crawl(WIKIPEDIA_API, "Wikipedia", "wiki", out_dir,
                  SEED_TOPICS, target_bytes, total, args.delay)

    print()
    print(f"  Corpus now ~{total/1024/1024:.1f} MB in {out_dir}")
    print("  Rebuilding search index...")

    # Rebuild the FTS index
    sys.path.insert(0, str(HERE))
    from server import knowledge
    stats = knowledge.build_index()
    print(f"  Indexed {stats['documents']} documents into {stats['passages']} passages.")
    print()
    print("  Done. PocketAI will use this knowledge base on its next launch.")
    print()


def _recent_wikinews_titles(limit: int = 200) -> list[str]:
    """Get recent Wikinews article titles (genuinely recent news)."""
    data = _api_get(WIKINEWS_API, {
        "action": "query",
        "list": "recentchanges",
        "rcnamespace": 0,
        "rctype": "new",
        "rclimit": min(limit, 500),
        "rcprop": "title",
    })
    changes = (data.get("query", {}) or {}).get("recentchanges", [])
    return [c["title"] for c in changes if "title" in c]


if __name__ == "__main__":
    main()
