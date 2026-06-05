# PocketAI – Product Plan

Created: 2026-06-04
Status: DRAFT

---

## Vision

A fully self-contained AI assistant that lives on a USB stick (or small device).
Plug it into any Windows PC, double-click one file, and within 15 seconds you have a
fully operational AI — chat, voice, and browser integration — with zero installation,
zero internet, zero admin rights required. Pull the stick out and the host machine is
left completely clean.

---

## What already exists (and why it falls short)

| Product | What it does | Gap |
|---|---|---|
| Portable-AI-USB (GitHub) | Ollama + AnythingLLM on USB | Requires Ollama installed on HOST — not portable |
| USB-Uncensored-LLM (GitHub) | llama.cpp binaries + models on USB | CLI only. No voice, no browser, no tray |
| Local_LLM_Notepad (GitHub) | Single .exe, no install, chat only | No voice, no browser extension, minimal UI |
| Docket Drive (commercial $69–99) | Pre-loaded USB, browser UI | Paid, no voice, no system tray, closed source |
| AnythingLLM Desktop | Great offline UI | Requires installation on host |

**The gap:** Nobody has combined portable inference + voice I/O + browser extension +
system tray into a single zero-install launcher. That is PocketAI.

---

## Core Experience (the golden path)

1. Plug in USB stick
2. Double-click `START POCKETAI.exe` on the stick
3. Tray icon appears: "PocketAI ready — Ctrl+Space to talk"
4. Browser opens to `http://localhost:7860` — chat interface
5. Press Ctrl+Space anywhere → voice input → spoken response
6. In Chrome/Firefox, sidebar button on every page → ask about any article, selection, or page
7. Pull out USB → everything disappears. Host machine untouched.

---

## Model Selection

### LLM (the brain)
**Recommended: Phi-4-mini-instruct Q4_K_M** (~2.3 GB)
- Microsoft's best small model — outperforms Llama 3.2 3B on most benchmarks
- 128K context window (can read entire documents)
- 8–15 tokens/sec on a modern CPU, no GPU needed
- Runs in 4–5 GB RAM (most machines can handle this)

**Power option: Qwen3-8B Q4_K_M** (~4.9 GB, needs 8 GB RAM)
- Better reasoning and coding than Phi-4-mini
- Still CPU-capable but slower (~5–8 tokens/sec)
- For users who want more intelligence and have a larger USB

**Could bundle both** — user picks at launch based on available RAM.

### Speech-to-Text
**Whisper.cpp tiny Q5** (~75 MB) — fast, accurate for English, runs in real-time
**Whisper.cpp small** (~244 MB) — multilingual, better accuracy, still fast

### Text-to-Speech
**Piper en_US-lessac-medium** (~63 MB) — neural TTS, natural-sounding, CPU-only, very fast

### Total footprint
- Phi-4-mini only: ~2.7 GB → fits on 8 GB USB 3.0
- Phi-4-mini + Qwen3-8B: ~7.5 GB → needs 16 GB USB 3.0
- With Whisper small + Piper: add ~307 MB

---

## Architecture

```
USB Root/
  START_POCKETAI.exe          ← Single launcher (Go binary, ~8 MB)

  pocketai/
    engines/
      llama-server.exe         ← llama.cpp HTTP server (OpenAI-compatible API)
      whisper-stream.exe       ← whisper.cpp real-time STT
      piper.exe                ← Piper TTS engine

    models/
      phi4-mini-q4.gguf        ← Primary LLM model
      qwen3-8b-q4.gguf         ← Optional power model
      whisper-small.bin        ← STT model
      en_US-lessac-medium.onnx ← TTS voice model

    ui/
      index.html               ← Chat web interface (self-contained, no CDN)
      extension/               ← Browser extension (Chrome + Firefox)

    data/                      ← Persisted on the USB
      chat_history.db          ← SQLite, all conversations saved here
      config.json              ← User preferences (model choice, hotkey, etc.)
```

### How it starts (launcher sequence)
1. `START_POCKETAI.exe` detects available RAM → picks Phi-4-mini or Qwen3
2. Spawns `llama-server.exe` pointing to model on USB (port 11434)
3. Spawns `whisper-stream.exe` (port 11435)
4. Serves web UI at `localhost:7860` from USB
5. Creates Windows system tray icon
6. Registers global hotkey (Ctrl+Space, configurable)
7. Opens browser to `http://localhost:7860`
8. On USB eject / launcher exit: kills all child processes cleanly

---

## Interfaces

### 1. Web Chat UI (localhost:7860)
- Clean, dark-mode chat interface
- Works in any browser, no extension needed
- Conversation history persisted in chat_history.db on the USB
- Model selector, voice toggle, settings panel
- "Read this page" — paste a URL or text, AI reads and responds
- Markdown rendering for code, lists, tables

### 2. System Tray + Global Hotkey
- Tray icon: right-click for quick query, quit, settings
- Ctrl+Space (default, configurable): opens a mini floating input box
  → type or speak → response appears as notification or in mini window
- Works from anywhere — no need to switch to the browser

### 3. Voice Mode (Whisper STT + Piper TTS)
- Toggle in web UI or via tray
- Push-to-talk (hold Ctrl+Space) or wake word
- Whisper transcribes mic → sent to LLM → Piper speaks the response
- Optional: always-on listening mode (mic icon in tray turns red)

### 4. Browser Extension (Chrome / Firefox / Edge)
- Sidebar button on every page
- Can send selected text, full page content, or typed query to local AI
- "Summarise this page", "Explain this paragraph", "Translate selection"
- Connects to `http://localhost:7860/api` — no external calls made
- Distributed via Chrome Web Store + Firefox Add-ons (localhost API is allowed)
- Falls back gracefully if PocketAI is not running (shows "Start PocketAI first")

---

## Tech Stack Decisions

| Component | Technology | Why |
|---|---|---|
| Launcher / orchestrator | **Go** | Single ~8MB binary, no runtime needed, great for spawning processes + HTTP server + OS tray |
| LLM inference | **llama.cpp** (llama-server) | C++ binary, CPU-optimised, OpenAI-compatible API, zero install |
| STT | **whisper.cpp** | C++ binary, same portability story, 47K GitHub stars |
| TTS | **Piper** | Fastest neural TTS for CPU, single binary + model file |
| Web UI | **Vanilla JS / HTML** | Zero build step, self-contained, no CDN dependencies |
| Browser extension | **Manifest V3 JS** | Works in Chrome, Firefox, Edge — no framework needed |
| Chat persistence | **SQLite** | Single file, no server needed, lives on the USB |
| Model format | **GGUF Q4_K_M** | Industry standard for portable quantised models |

---

## Session Memory & Preprompt System

This is one of PocketAI's strongest features — your AI remembers who you are across sessions,
because all memory lives on the USB stick itself and travels with you.

### How it works

**Every conversation** is stored in `data/chat_history.db` (SQLite) on the USB with a session ID,
timestamp, and full message log.

**At the start of each new session**, the system prompt sent to the LLM is assembled from three layers:

```
[1] CORE SYSTEM PROMPT]
"You are PocketAI, a private AI assistant..."

[2] USER MEMORIES (always included, explicit facts the user has saved)
- I am a software developer working primarily in Python and TypeScript
- My main project is called Rebusquero — an Argentine marketplace app
- I prefer concise answers with code examples
- My timezone is US Pacific

[3] PREVIOUS SESSION SUMMARY (last 1–3 sessions)
"In your last session (June 3): You were debugging a TypeScript build error in Azure
Functions. You resolved it by switching from CommonJS to ESM modules. You also discussed
the Investing1 paper trading bot — Instance 4 (Small Cap) was the best performer at +4.37%."
```

Phi-4-mini has a **128K token context window** — that is enormous. In practice:
- A user memory block = ~200 tokens
- A session summary = ~300–500 tokens
- You could include the last 10 full sessions verbatim before hitting the limit

### Three memory mechanisms

**1. Persistent Memories (always in context)**
User-defined facts. "Remember this" button extracts key facts from the current conversation
and saves them to `data/memories.json`. Included in every future session.
Examples: name, job, projects, preferences, recurring topics.

**2. Session Summaries (last N sessions)**
At the end of each session (or on demand), the AI generates a 3–5 sentence summary of
what was discussed. Stored in SQLite. The last 3 summaries are prepended to the next session.
This means the AI always has a sense of recent history without blowing up the context.

**3. Full Session Replay (optional, for deep continuity)**
A toggle: "Continue last session" loads the entire previous conversation verbatim into context.
At 128K tokens, that is ~96,000 words — longer than most novels. Virtually unlimited continuity.

### What this means in practice

- **First use:** AI knows nothing about you except the core prompt
- **After 1 session:** AI knows the topics you discussed
- **After a week:** AI remembers your projects, your preferences, your work in progress
- **After a month:** AI has a detailed picture of who you are — without any cloud sync, without any account

The AI on your USB stick becomes progressively more useful the more you use it.
The key insight: **the memory is on the stick, not in the cloud.**

### Storage impact
- Each conversation: ~5–50 KB (text only)
- 1 year of daily use: ~5–10 MB
- Negligible — even a 1 GB chat history partition lasts decades

---

## What Makes PocketAI Different

1. **Truly zero-install** — no admin rights, no registry writes, no host changes
2. **Voice I/O bundled** — not just text chat; speak in, hear responses out
3. **Browser extension** — AI in your browser without any cloud
4. **All history on the stick** — your conversations travel with you
5. **One launcher** — not three separate tools you have to wire together yourself
6. **Polished UX** — not a CLI or barebones interface

---

## USB / Hardware Options

### How big does the stick need to be?

| Configuration | Model files | Binaries + UI | Chat history buffer | **Total needed** | Recommended stick |
|---|---|---|---|---|---|
| Minimal (Phi-4-mini + Whisper tiny) | 2.37 GB | ~50 MB | 1 GB | **~3.5 GB** | 8 GB USB 3.0 |
| Standard (Phi-4-mini + Whisper small + Piper) | 2.61 GB | ~50 MB | 2 GB | **~5 GB** | 8 GB USB 3.0 |
| Power (+ Qwen3-8B as second model) | 7.5 GB | ~50 MB | 2 GB | **~10 GB** | 16 GB USB 3.0 |
| Full (all models + generous history) | 7.5 GB | ~50 MB | 5 GB | **~13 GB** | 32 GB USB 3.0 |

**Format:** exFAT (works on Windows, Mac, Linux without drivers)

**Speed matters for load time, not for inference:**
- Standard USB 3.0 flash drive (~400 MB/s): Phi-4-mini loads in 6–8 seconds
- USB 3.2 Gen 2 flash drive (~1 GB/s): loads in 2–3 seconds
- Samsung T7 / T9 portable SSD (~1 GB/s): loads in 2–3 seconds, much more reliable long-term

**Recommendation:** A quality 32 GB USB 3.2 stick (~$15–20) covers everything comfortably.
For daily use or a permanent setup, a Samsung T7 SSD ($50) is significantly faster and more durable.

---

## Minimum Hardware Requirements

### CPU
- **Minimum:** Any x86-64 CPU with **AVX2** support (Intel Haswell 2013+ / AMD Ryzen 2017+)
  AVX2 is critical — llama.cpp is 3–4x slower without it. Most machines made after 2015 have it.
- **Recommended:** Intel Core i5 10th gen+ or AMD Ryzen 5 3000+ (4+ performance cores)
- **Ideal:** Intel Core i7/i9 12th gen+ or AMD Ryzen 7 5000+ (8+ cores, faster DDR5 memory bandwidth)

### RAM
- **Minimum:** 8 GB — runs Phi-4-mini (4.2 GB) with enough left for OS
- **Recommended:** 16 GB — runs Phi-4-mini or Qwen3-8B comfortably, plus browser open
- **Ideal:** 32 GB — both models can be kept resident; instant switching

### GPU (completely optional — PocketAI runs CPU-only by default)
llama.cpp auto-detects any compatible GPU and offloads layers to VRAM automatically.

| GPU | VRAM | Speed vs CPU-only |
|---|---|---|
| No GPU | — | Baseline (8–15 tokens/sec on Phi-4-mini) |
| NVIDIA GTX 1660 / RTX 3060 | 6–12 GB | 3–5x faster (~30–60 tok/sec) |
| NVIDIA RTX 4060+ / 3080+ | 8–24 GB | 5–10x faster (~60–120 tok/sec) |
| AMD RX 6600+ (ROCm) | 8 GB+ | 3–5x faster |
| Apple M-series (Mac) | Unified | 4–8x faster (metal backend) |

A machine with **no GPU** is perfectly viable — response speed is usable, just not instant.
GPU acceleration activates automatically if present; nothing to configure.

### Storage on host machine
Zero — everything runs from the USB. The host machine needs no free disk space.

---

## Performance Expectations

| Model | Load time (USB 3.0 flash) | Load time (USB SSD) | Tokens/sec (CPU, modern laptop) |
|---|---|---|---|
| Phi-4-mini Q4 | ~6–8 sec | ~2–3 sec | 8–15 |
| Qwen3-8B Q4 | ~15–20 sec | ~5–7 sec | 4–8 |

Once loaded into RAM, speed is independent of USB read speed.
Voice latency (speech → response → speech): ~3–6 seconds on Phi-4-mini.

---

## Phase Plan

### Phase 1 – Core (MVP)
- Go launcher: starts llama-server, serves web UI, creates tray icon
- Web chat UI (vanilla JS, dark mode, markdown)
- Phi-4-mini model bundled
- Chat history in SQLite
- Windows only

### Phase 2 – Voice
- Integrate whisper.cpp for STT (push-to-talk)
- Integrate Piper for TTS
- Global hotkey (Ctrl+Space)
- Voice toggle in web UI

### Phase 3 – Browser Extension
- Chrome extension: sidebar, selection queries, page summarisation
- Firefox extension: same
- Auto-discovery of local PocketAI instance

### Phase 4 – Polish & Distribution
- Multi-model support (user picks at launch based on RAM)
- Mac + Linux launcher support
- Windows code signing (avoid antivirus false positives)
- Optional: wake word ("Hey Pocket")
- Optional: document upload (RAG over local files)

---

## Open Questions

1. **Windows only first, or cross-platform from day 1?**
   Go makes cross-platform easy, but tray icon / hotkey behaves differently on Mac/Linux.

2. **Model bundling strategy:** Ship with Phi-4-mini only, or let user download additional models?
   Bundling avoids a download step but makes the stick larger.

3. **Browser extension distribution:**
   Publish to Chrome Web Store (takes 1–3 days review), or self-signed sideload?
   Chrome Web Store is far better UX for end users.

4. **TTS language:** English-only (Piper en_US) or multilingual from day 1?

5. **Wake word vs push-to-talk:** Push-to-talk is simpler and less resource-intensive.
   Wake word requires always-on mic processing.

6. **Monetisation:** Free and open source? Or sell pre-configured USB drives?
   Could sell a physical "PocketAI Drive" with everything pre-loaded ($29–49).

---

## Competitive Risk

The main risk is that Ollama or LM Studio ship a "portable mode" — but neither has shown
interest in this. Their installs require admin and write to the host system by design.
The true portability angle (zero host footprint) is PocketAI's moat.
