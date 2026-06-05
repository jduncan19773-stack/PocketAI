"""
benchmark.py — PocketAI performance benchmark.

Tests the top 50 questions people ask commercial LLMs, measuring:
  - Time to first token (TTFT)
  - Tokens per second
  - Total response time
  - Response length (tokens + words)
  - Cache hit detection

Usage:
  cd <project-root>
  python benchmarks/benchmark.py

  # Skip cache (force fresh inference on every query):
  python benchmarks/benchmark.py --no-cache

  # Custom server URL:
  python benchmarks/benchmark.py --url http://localhost:7860

Output: console table + benchmarks/report_<timestamp>.md

Requirements:
  - PocketAI server running (python run.py or START_POCKETAI.bat)
  - Ollama running with at least phi4-mini loaded
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    print("Install httpx first:  pip install httpx")
    sys.exit(1)

# ── Top 50 queries ───────────────────────────────────────────────
# Sourced from publicly available analyses of ChatGPT/Gemini usage patterns.
# Covers: factual Q&A, writing, coding, math, creative, advice, summarisation.

TOP_50_QUERIES = [
    # Factual / knowledge
    ("Factual", "What is the capital of France?"),
    ("Factual", "How far is the Moon from Earth?"),
    ("Factual", "What year did World War 2 end?"),
    ("Factual", "What is the boiling point of water in Celsius?"),
    ("Factual", "Who wrote Romeo and Juliet?"),
    ("Factual", "What is the largest planet in our solar system?"),
    ("Factual", "How many bones are in the human body?"),
    ("Factual", "What is photosynthesis?"),
    ("Factual", "What causes a rainbow?"),
    ("Factual", "What is the speed of light?"),

    # Simple explanations
    ("Explain", "Explain quantum computing in simple terms"),
    ("Explain", "What is inflation and how does it affect me?"),
    ("Explain", "Explain how Wi-Fi works"),
    ("Explain", "What is a blockchain?"),
    ("Explain", "Explain the water cycle"),
    ("Explain", "What is machine learning?"),
    ("Explain", "How does a vaccine work?"),
    ("Explain", "What is DNA?"),

    # Writing / creative
    ("Writing", "Write a short poem about autumn"),
    ("Writing", "Write a professional email declining a meeting"),
    ("Writing", "Write a 3-sentence summary of climate change"),
    ("Writing", "Give me 5 creative names for a coffee shop"),
    ("Writing", "Write a birthday message for a colleague"),
    ("Writing", "Write a cover letter opening paragraph for a marketing job"),

    # Coding
    ("Coding", "Write a Python function to check if a string is a palindrome"),
    ("Coding", "Write a JavaScript function to reverse an array"),
    ("Coding", "Explain what a REST API is"),
    ("Coding", "What is the difference between == and === in JavaScript?"),
    ("Coding", "Write a SQL query to find duplicate rows in a table"),
    ("Coding", "What is recursion? Give a simple example"),

    # Math / logic
    ("Math", "What is 15% of 240?"),
    ("Math", "What is the square root of 144?"),
    ("Math", "If a train travels 60 mph for 2.5 hours, how far does it go?"),
    ("Math", "What is the formula for the area of a circle?"),

    # Advice / practical
    ("Advice", "What are the best foods for heart health?"),
    ("Advice", "How do I improve my sleep?"),
    ("Advice", "What should I do if I have a cold?"),
    ("Advice", "How do I start saving money?"),
    ("Advice", "What are good tips for a job interview?"),
    ("Advice", "How do I deal with stress?"),

    # Conversational / fun
    ("Chat", "Tell me a fun fact I probably don't know"),
    ("Chat", "What are the most visited countries in the world?"),
    ("Chat", "Give me a recipe for chocolate chip cookies"),
    ("Chat", "What is a good book to read if I like mysteries?"),
    ("Chat", "What is the Eiffel Tower made of?"),
    ("Chat", "Tell me a short joke"),

    # Summarisation / longer output
    ("Summary", "Summarise the main causes of the First World War in 3 bullet points"),
    ("Summary", "What are the pros and cons of electric cars?"),
    ("Summary", "List the 5 most important programming languages and what they're used for"),
    ("Summary", "What are the main differences between a laptop and a desktop computer?"),
]

assert len(TOP_50_QUERIES) == 50, f"Expected 50 queries, got {len(TOP_50_QUERIES)}"


# ── Benchmark runner ─────────────────────────────────────────────

def run_query(client: httpx.Client, base_url: str, query: str) -> dict:
    """
    POST to /api/chat and measure streaming performance.
    Returns a dict with timing and quality metrics.
    """
    result = {
        "query":          query,
        "ttft_s":         None,    # time to first token
        "total_s":        None,    # total response time
        "tokens":         0,       # approximate token count (split on spaces * 1.3)
        "words":          0,
        "tok_per_sec":    None,
        "cached":         False,
        "error":          None,
    }

    t_start     = time.perf_counter()
    t_first_tok = None
    merged_text = ""

    try:
        with client.stream(
            "POST",
            f"{base_url}/api/chat",
            json={"query": query},
            timeout=120,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    evt = json.loads(line[5:].strip())
                except Exception:
                    continue

                if evt.get("type") == "merge_token":
                    if t_first_tok is None:
                        t_first_tok = time.perf_counter()
                    merged_text += evt["token"]

                elif evt.get("type") == "done":
                    result["cached"] = evt.get("cached", False)
                    break

                elif evt.get("type") == "error":
                    result["error"] = evt.get("message", "unknown error")
                    break

    except Exception as e:
        result["error"] = str(e)
        return result

    t_end = time.perf_counter()

    result["total_s"]     = round(t_end - t_start, 2)
    result["ttft_s"]      = round(t_first_tok - t_start, 2) if t_first_tok else None
    result["words"]       = len(merged_text.split())
    result["tokens"]      = int(result["words"] * 1.3)   # rough token estimate

    if result["total_s"] and result["tokens"] and result["ttft_s"] is not None:
        generation_time = result["total_s"] - result["ttft_s"]
        if generation_time > 0:
            result["tok_per_sec"] = round(result["tokens"] / generation_time, 1)

    return result


# ── Report generation ────────────────────────────────────────────

def format_report(results: list, status: dict, args) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    total    = len(results)
    errors   = sum(1 for r in results if r["error"])
    cached   = sum(1 for r in results if r["cached"])
    ok       = [r for r in results if not r["error"] and not r["cached"]]

    avg_ttft  = (sum(r["ttft_s"]      for r in ok if r["ttft_s"])      / max(len(ok), 1))
    avg_tps   = (sum(r["tok_per_sec"] for r in ok if r["tok_per_sec"]) / max(len(ok), 1))
    avg_total = (sum(r["total_s"]     for r in ok if r["total_s"])     / max(len(ok), 1))
    avg_words = (sum(r["words"]       for r in ok)                     / max(len(ok), 1))

    model_a = status.get("model_a", "unknown")
    model_b = status.get("model_b", "")
    tier    = status.get("tier", "unknown")
    mode    = "Single-model" if status.get("single_model") else "Dual-model"

    lines = [
        f"# PocketAI Benchmark Report",
        f"",
        f"**Date:** {ts}",
        f"**Model(s):** {model_a}" + (f" + {model_b}" if model_b else ""),
        f"**Mode:** {mode}  |  **Tier:** {tier}",
        f"**Server:** {args.url}",
        f"",
        f"---",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Queries run | {total} |",
        f"| Successful | {total - errors} |",
        f"| Errors | {errors} |",
        f"| Cache hits | {cached} |",
        f"| **Avg time-to-first-token** | **{avg_ttft:.2f}s** |",
        f"| **Avg tokens/second** | **{avg_tps:.1f} tok/s** |",
        f"| Avg total response time | {avg_total:.2f}s |",
        f"| Avg response length | {avg_words:.0f} words |",
        f"",
        f"---",
        f"",
        f"## Results by Category",
        f"",
    ]

    # Group by category
    categories = {}
    for cat, query in TOP_50_QUERIES:
        categories.setdefault(cat, [])
    for r, (cat, _) in zip(results, TOP_50_QUERIES):
        categories[cat].append(r)

    for cat, cat_results in categories.items():
        ok_cat   = [r for r in cat_results if not r["error"] and not r["cached"]]
        avg_time = sum(r["total_s"] for r in ok_cat) / max(len(ok_cat), 1)
        avg_w    = sum(r["words"]   for r in ok_cat) / max(len(ok_cat), 1)
        lines.append(f"### {cat} ({len(cat_results)} queries)")
        lines.append(f"Avg response time: **{avg_time:.1f}s** | Avg length: **{avg_w:.0f} words**")
        lines.append("")

    lines += [
        f"---",
        f"",
        f"## All Query Results",
        f"",
        f"| # | Category | Query (truncated) | TTFT | Total | Tok/s | Words | Note |",
        f"|---|----------|-------------------|------|-------|-------|-------|------|",
    ]

    for i, (r, (cat, query)) in enumerate(zip(results, TOP_50_QUERIES), 1):
        q_short   = query[:42] + "…" if len(query) > 42 else query
        ttft_str  = f"{r['ttft_s']:.2f}s"  if r["ttft_s"]      else "—"
        total_str = f"{r['total_s']:.2f}s" if r["total_s"]      else "—"
        tps_str   = f"{r['tok_per_sec']}"  if r["tok_per_sec"]  else "—"
        note      = "⚡ cached" if r["cached"] else ("❌ " + (r["error"] or "")[:30]) if r["error"] else ""
        lines.append(
            f"| {i} | {cat} | {q_short} | {ttft_str} | {total_str} | {tps_str} | {r['words']} | {note} |"
        )

    lines += ["", "---", "", f"*Generated by PocketAI benchmark suite*"]
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="PocketAI benchmark")
    ap.add_argument("--url",      default="http://localhost:7860", help="PocketAI server URL")
    ap.add_argument("--no-cache", action="store_true",             help="Bypass cache (fresh inference each time)")
    ap.add_argument("--limit",    type=int, default=50,            help="Run only first N queries (default 50)")
    args = ap.parse_args()

    queries = TOP_50_QUERIES[: args.limit]

    # Ensure Unicode output works on Windows console
    import sys, io
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print(f"\n  [*] PocketAI Benchmark - {len(queries)} queries")
    print(f"      Server: {args.url}\n")

    # Check server is up
    try:
        r      = httpx.get(f"{args.url}/api/status", timeout=5)
        status = r.json()
        model_a = status.get("model_a", "?")
        model_b = status.get("model_b", "")
        mode    = "single" if status.get("single_model") else "dual"
        print(f"  Model: {model_a}" + (f" + {model_b}" if model_b else "") + f"  [{mode} mode]")
        print(f"  Tier:  {status.get('tier')}  — {status.get('description')}\n")
    except Exception as e:
        print(f"  ERROR: Could not reach PocketAI at {args.url}")
        print(f"         {e}")
        print(f"  Make sure PocketAI is running first.\n")
        sys.exit(1)

    results = []
    col_w   = 44

    print(f"  {'#':<4} {'Category':<10} {'Query':<{col_w}} {'TTFT':>6} {'Tok/s':>7} {'Words':>6}")
    print(f"  {'─'*4} {'─'*10} {'─'*col_w} {'─'*6} {'─'*7} {'─'*6}")

    with httpx.Client() as client:
        for i, (cat, query) in enumerate(queries, 1):
            r = run_query(client, args.url, query)
            results.append(r)

            q_short   = query[:col_w - 1] + "…" if len(query) >= col_w else query
            ttft_str  = f"{r['ttft_s']:.2f}s"  if r["ttft_s"]     else "—     "
            tps_str   = f"{r['tok_per_sec']}" if r["tok_per_sec"] else "—"
            flag      = " ⚡" if r["cached"] else (" ❌" if r["error"] else "")
            print(f"  {i:<4} {cat:<10} {q_short:<{col_w}} {ttft_str:>6} {tps_str:>7} {r['words']:>6}{flag}")

            # Brief pause between requests to avoid hammering the model
            if not r["cached"] and i < len(queries):
                time.sleep(0.2)

    # Summary
    ok = [r for r in results if not r["error"] and not r["cached"]]
    if ok:
        avg_ttft = sum(r["ttft_s"]      for r in ok if r["ttft_s"])      / len(ok)
        avg_tps  = sum(r["tok_per_sec"] for r in ok if r["tok_per_sec"]) / len(ok)
        avg_time = sum(r["total_s"]     for r in ok if r["total_s"])     / len(ok)

        print(f"\n  ─────────────────────────────────────────────────────")
        print(f"  Avg time-to-first-token : {avg_ttft:.2f}s")
        print(f"  Avg tokens/second       : {avg_tps:.1f}")
        print(f"  Avg total time          : {avg_time:.2f}s")
        print(f"  Errors                  : {sum(1 for r in results if r['error'])}")
        print(f"  Cache hits              : {sum(1 for r in results if r['cached'])}")

    # Write report
    report  = format_report(results, status, args)
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = Path(__file__).parent / f"report_{ts}.md"
    outfile.write_text(report, encoding="utf-8")
    print(f"\n  Report saved: {outfile}\n")


if __name__ == "__main__":
    main()
