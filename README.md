# ◈ PocketAI — a private AI that lives on a USB stick

**Plug it into any Windows PC. Double-click one file. ~15 seconds later you're talking to your own AI — no installation, no internet, no account, no admin rights. Pull the stick out and the machine is left completely clean.**

PocketAI is a fully self-contained AI assistant: two language models, a chat interface, voice in and out, and a persistent memory — all living on a 32 GB flash drive. Nothing you say ever leaves the room.

---

## Why this exists

Cloud AI assistants are remarkable, but they come with strings: an account, a subscription, an internet connection, and every conversation stored on someone else's servers. There are real situations where none of that is acceptable — or possible:

- **Privacy that's structural, not a policy.** Medical questions, legal drafts, financial details, a private journal. PocketAI can't leak your conversations because there is no server to leak them from — no telemetry, no cloud sync, no account.
- **Places the internet doesn't reach.** Flights, ships, rural areas, secure facilities, offline labs — after one-time setup, PocketAI needs zero connectivity, forever.
- **People that tech forgot.** The UI is deliberately designed for a non-technical 80-year-old: large text, a simple layout, and voice-first interaction. Talk to it; it talks back. My original test user was a grandparent, and it shows in every design decision.
- **Machines you don't own.** A borrowed laptop, a work PC, a library computer. PocketAI writes nothing to the host — no registry entries, no installs. All sessions, memories, and cache live in `data/` on the stick.

## What makes it interesting under the hood

**Two models answer every question — in parallel.** PocketAI runs two different LLMs simultaneously (e.g. Microsoft's Phi-4-mini alongside Qwen3) and merges their answers with a deterministic, sub-10 ms algorithm: high-overlap responses collapse to the stronger one; divergent responses get the second model's novel sentences appended. Different training data, different blind spots — a merged answer is more complete than either alone, and because they run concurrently it costs ~zero extra latency. You watch both models "thinking" live in the UI.

**It adapts to whatever machine you plug into.** Hardware is auto-detected at startup and the model tier scales accordingly:

| Host machine | Models selected | Experience |
|---|---|---|
| ≥ 20 GB RAM (GPU) | phi4-mini (GPU) + qwen3:8b | Fastest, dual-model |
| ≥ 10 GB RAM | phi4-mini + qwen3:8b (CPU) | Dual-model |
| ≥ 5 GB RAM — **the design target: an i3 with 8 GB** | phi4-mini + qwen3:4b | Dual-model |
| < 5 GB RAM | phi4-mini + gemma2:2b (or single-model) | Still works |

**It remembers you — on the stick, nowhere else.** Sessions auto-title and persist; summaries are generated automatically after a few messages; you can save long-term memories that get injected into every future conversation. A two-level SQLite cache (exact hash + fuzzy keyword match at 82% Jaccard similarity) makes repeated and paraphrased questions answer instantly.

**No frameworks, no CDNs, no dependencies that phone home.** FastAPI + SSE streaming on the backend; vanilla HTML/CSS/JS on the front; voice via the browser's built-in Web Speech APIs; markdown rendered by a locally-bundled library. The whole UI works with the network cable cut.

## What it's intended to run on

- **Host:** any Windows 10/11 PC from roughly the last decade — the design target is a humble **i3 with 8 GB RAM**; it simply gets faster and smarter on better hardware. GPU optional.
- **Stick:** a 32 GB USB drive (models + engine are ~5–6 GB; the rest is headroom for your data). USB 3.0 recommended for load speed — inference itself runs in host RAM/CPU, so the stick is read-mostly after startup.
- **Browser:** Chrome or Edge for voice features (they ship the Web Speech API); any modern browser for text chat.

## Quick start

```
1. SETUP.bat            ← one-time, with internet: downloads engine + models (~5–6 GB)
2. START_POCKETAI.bat   ← every time after: fully offline, opens localhost:7860
```

That's the whole manual.

## Current status & honest limitations

Version 0.2 — core experience (dual-model chat, voice in/out, sessions, memories, cache, USB deployment) is complete and working. Known gaps, all on the roadmap: Python is currently required on the host (a bundled embeddable Python is planned), voice input needs Chrome/Edge (Whisper.cpp integration planned), and Mac/Linux launchers are planned. See [docs/STATUS.md](docs/STATUS.md) for the full build status and [docs/DESIGN.md](docs/DESIGN.md) for the architecture deep-dive.

---

*Built solo by [Jonathan Duncan](https://www.linkedin.com/in/jonathan-duncan-a2b2a910) — ex-Microsoft GM building AI products end-to-end. Python · FastAPI · llama.cpp/Ollama · SQLite · vanilla JS.*
