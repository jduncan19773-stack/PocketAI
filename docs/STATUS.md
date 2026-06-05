# PocketAI — Build Status

**Updated:** 2026-06-04
**Version:** 0.2.0-dev

---

## Overall Status: ✅ Phase 1 + 2 Complete, Phase 3 In Progress

| Phase | Feature | Status | Notes |
|-------|---------|--------|-------|
| 1 | FastAPI backend + SSE streaming | ✅ Done | Endpoints: /api/chat, /api/status, /api/sessions, /api/memories |
| 1 | Dual-model parallel inference | ✅ Done | asyncio.gather, OpenAI-compatible API |
| 1 | Single-model mode (8 GB RAM) | ✅ Done | Auto-detected, phi4-mini only below 8 GB free |
| 1 | SQLite cache (exact + fuzzy) | ✅ Done | 82% Jaccard threshold for fuzzy match |
| 1 | Session history | ✅ Done | SQLite, persists on USB |
| 1 | Session summaries | ✅ Done | Auto-generated after 4+ messages |
| 1 | Long-term memories | ✅ Done | User-saved notes, injected in every session |
| 1 | Web UI (dark, responsive) | ✅ Done | Vanilla JS, no framework, works offline |
| 1 | Markdown rendering | ✅ Done | marked.js bundled locally |
| 1 | Session sidebar | ✅ Done | Last 30 sessions, auto-titled |
| 2 | Voice input (mic) | ✅ Done | Web Speech API (Chrome/Edge) |
| 2 | Voice output (spoken responses) | ✅ Done | Web Speech Synthesis, toggle button |
| 2 | START_POCKETAI.bat launcher | ✅ Done | Auto-detects USB vs Ollama mode |
| 2 | SETUP.bat (first-time download) | ✅ Done | Downloads llama-server + GGUFs |
| 2 | llama-server.exe process manager | ✅ Done | USB mode, ports 11434/11435 |
| 3 | Browser extension | ⬜ Planned | Chrome/Firefox/Edge sidebar |
| 4 | Windows code signing | ⬜ Planned | Avoid AV false positives |
| 4 | Mac/Linux launcher | ⬜ Planned | Cross-platform via shell script |

---

## Known Limitations

| Issue | Severity | Plan |
|-------|----------|------|
| Voice input requires Chrome or Edge (Web Speech API) | Low | Whisper.cpp integration planned for Phase 4 |
| Ollama mode requires Ollama installed on host | Medium | SETUP.bat downloads llama-server for true zero-install |
| Python required on host for launcher | Medium | Python Embeddable can be bundled on USB (~30 MB) |
| Voice quality depends on OS voice packs | Low | Piper TTS integration planned for Phase 4 |

---

## File Structure

```
PocketAI/
├── START_POCKETAI.bat     ← Double-click to launch (auto-detects mode)
├── SETUP.bat              ← First-time download (llama-server + models)
├── run.py                 ← Python launcher (USB mode)
├── start.py               ← Python launcher (Ollama mode, handles model pulling)
├── setup_download.py      ← Downloads AI binaries and models
├── requirements.txt       ← Python dependencies
│
├── server/
│   ├── main.py            ← FastAPI app, all HTTP endpoints
│   ├── inference.py       ← Dual/single-model inference, streaming
│   ├── config.py          ← Hardware detection, model tier selection
│   ├── memory.py          ← Session + memory SQLite storage
│   ├── cache.py           ← Two-level query cache
│   └── launcher.py        ← llama-server.exe process manager (USB mode)
│
├── ui/
│   ├── index.html         ← Chat interface
│   ├── app.js             ← Frontend logic, SSE client, voice I/O
│   ├── style.css          ← Dark theme, responsive layout
│   └── marked.min.js      ← Markdown renderer (bundled, offline)
│
├── bin/                   ← AI engine binaries (USB mode, downloaded by SETUP)
│   └── llama-server.exe
│
├── models/                ← GGUF model files (downloaded by SETUP, ~5 GB)
│   ├── phi4-mini-q4_k_m.gguf
│   └── qwen3-4b-q4_k_m.gguf
│
├── data/                  ← All user data (stays on USB)
│   ├── sessions.db        ← Chat history + memories
│   └── cache.db           ← Response cache
│
├── benchmarks/
│   └── benchmark.py       ← Performance tests (top 50 queries)
│
└── docs/
    ├── STATUS.md          ← This file
    └── DESIGN.md          ← Architecture and design decisions
```

---

## Hardware Requirements

| RAM | Tier | Models | Tokens/sec (est.) |
|-----|------|--------|-------------------|
| 8 GB | Low (single) | phi4-mini only | 8–15 tok/s |
| 16 GB | Medium (dual) | phi4-mini + qwen3:4b | 5–10 tok/s |
| 32 GB | High (dual) | phi4-mini + qwen3:8b | 4–8 tok/s |

CPU minimum: x86-64 with AVX2 (Intel Haswell 2013+ / AMD Ryzen 2017+).
GPU: optional — llama.cpp auto-detects and offloads layers to VRAM.

---

## Performance Baseline (Ollama, i3-12100, 16 GB DDR4)

Measured 2026-06-04. See `benchmarks/` for full results.

| Metric | Value |
|--------|-------|
| Avg time-to-first-token | ~1.2 s |
| Avg tokens/second (phi4-mini) | 11.4 tok/s |
| Cache hit rate (after warm-up) | ~35% |
| Avg response length | 187 tokens |
| Server startup time | < 3 s |
