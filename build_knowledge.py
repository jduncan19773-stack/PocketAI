"""
build_knowledge.py — Fill PocketAI's offline knowledge base.

Pulls plain-text article extracts from Wikipedia and Wikinews (both have
official APIs and openly-licensed content) and saves them as text files in
the knowledge corpus. Then rebuilds the search index so PocketAI can use them.

It crawls breadth-first from a set of seed topics (recent events + major
reference subjects), following article links, until the target size is hit.

Usage:
  python build_knowledge.py                  # default ~150 MB into knowledge/corpus
  python build_knowledge.py --target-mb 3000 # fill to ~3 GB
  python build_knowledge.py --seed           # build the small committed seed set
  python build_knowledge.py --target-mb 20 --seed

Sources:
  Wikipedia  (en.wikipedia.org)  — CC BY-SA
  Wikinews   (en.wikinews.org)   — CC BY  (genuinely recent news)

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


def main():
    ap = argparse.ArgumentParser(description="Build PocketAI's knowledge base")
    ap.add_argument("--target-mb", type=int, default=150,
                    help="Total corpus size to aim for, in MB (default 150)")
    ap.add_argument("--seed", action="store_true",
                    help="Write into knowledge/seed (the small committed set) instead of corpus/")
    ap.add_argument("--delay", type=float, default=0.3,
                    help="Seconds between API calls (politeness; default 0.3)")
    args = ap.parse_args()

    out_dir = SEED_DIR if args.seed else CORPUS_DIR
    target_bytes = args.target_mb * 1024 * 1024

    print()
    print("  ============================================================")
    print("   PocketAI — Knowledge Base Builder")
    print("  ============================================================")
    print(f"  Target: ~{args.target_mb} MB into {out_dir}")
    print(f"  Sources: Wikipedia + Wikinews (openly licensed)")
    print()

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
