# PocketAI — Architecture & Design Document

**Created:** 2026-06-04
**Author:** PocketAI project

---

## Vision

A fully self-contained AI assistant that lives on a USB stick.
Plug it into any Windows PC, double-click one file, and within 15 seconds you have
a private AI — chat, voice, persistent memory — with zero installation, zero internet,
zero admin rights. Pull the stick out and the host machine is left completely clean.

---

## Design Principles

1. **Zero host footprint** — no registry writes, no installs, all data on USB
2. **Offline first** — works with no internet connection after one-time setup
3. **Accessible UX** — designed for 80-year-olds, large text, voice-first, simple layout
4. **Progressive capability** — works on 8 GB RAM i3; gets better on bigger machines
5. **Transparent AI** — dual-model shown in UI; user sees both models thinking
6. **Private by design** — no telemetry, no cloud sync, no accounts

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        USB Stick                        │
│                                                         │
│  START_POCKETAI.bat                                     │
│       │                                                 │
│       ▼                                                 │
│  start.py / run.py ──► launcher.py                      │
│                              │                          │
│                    ┌─────────┴──────────┐               │
│                    ▼                    ▼               │
│             llama-server A       llama-server B         │
│             (phi4-mini)          (qwen3:4b)             │
│             port 11434           port 11435             │
│                    │                    │               │
│                    └─────────┬──────────┘               │
│                              ▼                          │
│                    ┌─────────────────┐                  │
│                    │  FastAPI Server  │                  │
│                    │  (uvicorn :7860) │                  │
│                    └────────┬────────┘                  │
│                             │                           │
│              ┌──────────────┼──────────────┐            │
│              ▼              ▼              ▼            │
│         inference.py    memory.py      cache.py         │
│         (dual model     (SQLite:       (SQLite:         │
│          parallel +     sessions,      exact +          │
│          SSE stream)    memories)      fuzzy)           │
│              │                                          │
│              ▼                                          │
│    Browser: localhost:7860                              │
│    ┌────────────────────────────────┐                   │
│    │  index.html / app.js / style   │                   │
│    │  Voice in:  Web Speech API     │                   │
│    │  Voice out: Speech Synthesis   │                   │
│    │  Markdown:  marked.js (local)  │                   │
│    └────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────┘
```

---

## Inference Architecture

### Dual-Model Mode (16 GB+ RAM)

```
User query
    │
    ├──► Model A (phi4-mini)    token_a SSE events ──► UI shows model A typing
    │         │
    └──► Model B (qwen3:4b)    token_b SSE events ──► UI shows model B typing
              │
    Both complete
              │
              ▼
    Fast merge (< 10ms, deterministic, no extra model call)
    ┌─────────────────────────────────────────────────────┐
    │ overlap_ratio(A, B) > 0.75 → return longer response │
    │ otherwise → A as base, append novel sentences from B│
    └─────────────────────────────────────────────────────┘
              │
    Merged response ──► merge_token SSE events ──► UI renders final answer
```

### Single-Model Mode (< 8 GB free RAM)

```
User query ──► phi4-mini ──► tokens stream directly as merge_token events
```
No merging, no second model. Same UI experience, just one model badge shown.

### Why dual-model?
- Models have different training data and reasoning styles
- Running them in parallel adds ~0 latency (both run simultaneously)
- Merged answers are more complete and catch each other's blind spots
- Users see both "thinking" — makes the AI feel more alive and transparent

---

## Cache Design

Two-level SQLite cache in `data/cache.db`:

**Level 1 — Exact match:**
Hash = SHA-256(normalised_query + model_a + model_b)
Lookup is O(1), indexed on hash column.

**Level 2 — Fuzzy match:**
Jaccard similarity of keyword sets (stopwords removed).
Threshold: 82% — catches paraphrased questions ("What is X?" vs "Tell me about X").
Scans last 2,000 entries (fast in practice, < 5ms).

Cache hit → response streams instantly as tokens. Still saves to session history.

---

## Memory System

Three layers of context injected into every system prompt:

```
[1] Core identity prompt
    "You are PocketAI, a private offline AI assistant..."

[2] User memories (always included)
    - My name is Margaret
    - I live in Dublin
    - I prefer short, plain-English answers

[3] Recent session summaries (last 3 sessions, ~300 tokens each)
    "In your last session: you discussed recipe ideas for a dinner party.
     You asked about vegetarian options. The AI suggested risotto."
```

Session summaries are auto-generated in a background task after every session
reaches 4+ messages (2 exchanges). Generation uses phi4-mini with a 2-3 sentence
summary prompt — adds ~2-3 seconds but runs non-blocking after the response is done.

phi4-mini has a 128K token context window. Practical limits:
- User memory block:      ~200 tokens
- Session summary:        ~400 tokens
- 3 summaries:           ~1,200 tokens
- Available for history: ~126,000 tokens (~100K words)

---

## API Compatibility Layer

inference.py uses the OpenAI-compatible `/v1/chat/completions` endpoint (SSE).
This is supported by:
- **Ollama** (>= 0.1.14) — development and host-installed mode
- **llama.cpp llama-server** — USB-bundled offline mode

The model "name" field in requests:
- Ollama: uses the name to route to the correct model
- llama-server: ignores the name (model is loaded at startup from the GGUF path)

In USB mode, model A runs on port 11434 and model B on port 11435. inference.py
sends model A requests to `LLM_HOST_A` and model B requests to `LLM_HOST_B`.

---

## Model Selection Logic

```python
if available_gb >= 18:  → phi4-mini + qwen3:8b  (high)
elif available_gb >= 8: → phi4-mini + qwen3:4b  (medium)
else:                   → phi4-mini only         (low, single model)
```

Checked at every request — adapts if RAM changes during a session (e.g. other
apps open/close). On 8 GB machines: phi4-mini alone uses ~2.5 GB RAM, leaving
5+ GB for Windows + browser.

---

## USB Storage Layout

```
USB:/
  START_POCKETAI.bat   ← Double-click to launch
  SETUP.bat            ← First-time download script
  run.py               ← Python launcher (USB mode)
  start.py             ← Python launcher (Ollama mode)
  setup_download.py    ← Downloads AI engine + models
  requirements.txt

  server/              ← Python backend
  ui/                  ← Web interface (HTML/CSS/JS)

  bin/
    llama-server.exe   ← llama.cpp HTTP server (~50 MB, downloaded)

  models/
    phi4-mini-q4_k_m.gguf    ← ~2.3 GB (always required)
    qwen3-4b-q4_k_m.gguf    ← ~2.5 GB (optional, 16 GB+ machines)

  data/                ← Created on first launch, stays on USB
    sessions.db        ← All chat history + memories
    cache.db           ← Cached responses

  venv/                ← Python virtual environment (created on first launch)
  pycache/             ← Redirected .pyc files (stays on USB)
```

Total USB space needed:
- Minimal (phi4-mini only):  ~4.5 GB
- Standard (dual model):     ~7.0 GB
- With 2 GB data buffer:     ~9.0 GB
- Recommended USB:           32 GB USB 3.0+

---

## Voice Implementation

**Input:** Web Speech API (`SpeechRecognition`)
- Built into Chrome and Edge — no downloads, no server
- Push-to-talk: click mic button → speak → auto-sends when speech ends
- Error messages translated to plain English (e.g. "Microphone blocked")

**Output:** Web Speech Synthesis (`speechSynthesis`)
- Built into all modern browsers — no downloads
- Toggle with 🔊 button (speaker icon in input bar)
- Strips markdown before speaking (no "hashtag hashtag title" weirdness)
- Prefers "Google" or "Natural" voices if available

**Future (Phase 4):**
- Replace Web Speech API with Whisper.cpp (~75 MB binary, works offline in all browsers)
- Replace Speech Synthesis with Piper TTS (~63 MB, much more natural voice)

---

## Tech Stack Rationale

| Component | Choice | Why |
|-----------|--------|-----|
| Backend | Python + FastAPI | asyncio SSE streaming, async SQLite, easy to run from USB venv |
| Inference | llama.cpp (USB) / Ollama (host) | Both expose OpenAI-compatible API; llama.cpp = zero install |
| UI | Vanilla JS | Zero build step, works offline, no CDN, tiny file size |
| Markdown | marked.js (bundled) | 50 KB, works offline, GitHub-flavour markdown |
| Cache | SQLite + aiosqlite | Zero-config, single file, lives on USB |
| Sessions | SQLite + aiosqlite | Same — zero footprint |
| Voice in | Web Speech API | Built into Chrome/Edge, no extra files |
| Voice out | Web Speech Synthesis | Built into all browsers, no extra files |

---

## Security Model

- All inference is local — no data leaves the device
- No authentication (single-user, physical USB access = authorization)
- Cache and sessions contain conversation history — treat USB as sensitive
- HTTP only (localhost) — no TLS needed for loopback
- No CORS policy issues — all served from same origin (localhost:7860)

---

## Open Questions / Future Work

1. **Python embeddable on USB** — bundle Python Embeddable (~30 MB) to eliminate the Python-on-host requirement entirely
2. **Piper TTS** — neural TTS, much more natural than browser synthesis
3. **Whisper STT** — offline speech recognition, works in all browsers
4. **Browser extension** — sidebar on any webpage, "summarise this page"
5. **Document upload (RAG)** — let user drop in PDFs, search against them locally
6. **Wake word** — "Hey Pocket" with always-on listening (needs Whisper)
7. **Cross-platform** — Mac/Linux .sh launcher, same USB works everywhere
8. **Windows code signing** — prevents SmartScreen / AV false positives on llama-server.exe
