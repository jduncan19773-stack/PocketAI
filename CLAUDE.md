# PocketAI – Claude Instructions & Project Decisions

## IMPORTANT: Read These Files First — MANDATORY
At the start of EVERY session, BEFORE doing ANYTHING else, read both of these files in full:
1. `tasks/plan.md` — full product vision, architecture, and design decisions
2. `tasks/progress.txt` — what is built, what remains, all credentials and resource names

Do not respond to any request, write any code, or take any action without reading both files first.
They are the single source of truth for this project.

---

## Working Instructions

1. First think through the problem, read the codebase for relevant files, and write a plan to tasks/todo.md.
2. The plan should have a list of todo items that you can check off as you complete them.
3. Before you begin working, check in with me and I will verify the plan.
4. Then, begin working on the todo items, marking them as complete as you go.
5. Every step of the way give me a high level explanation of what changes you made and why.
6. Make every task and code change as simple as possible. Avoid massive or complex changes. Every change should impact as little code as possible. Everything is about simplicity.
7. Finally, add a review section to the todo.md file with a summary of the changes made.
8. DO NOT BE LAZY. NEVER BE LAZY. IF THERE IS A BUG FIND THE ROOT CAUSE AND FIX IT. NO TEMPORARY FIXES. YOU ARE A SENIOR DEVELOPER.
9. MAKE ALL FIXES AND CODE CHANGES AS SIMPLE AS HUMANLY POSSIBLE. THEY SHOULD ONLY IMPACT NECESSARY CODE. YOUR GOAL IS TO NOT INTRODUCE ANY BUGS. IT'S ALL ABOUT SIMPLICITY.
10. When finished editing any code file, clearly document how the file works in comments, above each method and variable declaration, in plain English.
11. At the start of each new feature, create a git branch with a descriptive name. Work on that branch for the entire feature.
12. Once functionality is complete and bug-free, create a git commit with a clear message. Only commit working, tested code.
13. If multiple jobs can be done at once, create up to 10 subagents running concurrently — but ensure no logic clashes or bugs from merging agents' work. Maximum concurrency: 10 threads.
14. ALWAYS inspect external API documentation for bulk/batch endpoints before implementing features that make multiple API calls.
15. Update this CLAUDE.md file every time a project-wide decision is made.
16. NEVER ask the user to open, read, or navigate to URLs. Do all external research yourself using available tools.
17. After completing any deployment, always update tasks/progress.txt with what was done.
18. NEVER ask permission to read, edit, or write files locally. Full agency is granted — just do it.

---

## Project: PocketAI

*(Vision and product description to be filled in during planning — see tasks/plan.md)*

---

## Project-Wide Tech Decisions

### Stack
- **Backend:**    Python 3.10+ + FastAPI + uvicorn (async, SSE streaming)
- **Inference:**  Ollama (model server, OpenAI-compatible API, GGUF models)
- **Parallelism:**asyncio.gather — both models run simultaneously, tokens streamed live
- **Cache:**      SQLite (aiosqlite) — exact + fuzzy keyword match, lives in data/ on USB
- **Memory:**     SQLite — sessions, messages, user memories, rolling session summaries
- **UI:**         Vanilla HTML/CSS/JS — zero framework, works offline, no CDN
- **Voice in:**   Web Speech API (SpeechRecognition) — built into Chrome/Edge, no deps
- **Voice out:**  Web Speech Synthesis — built in, no deps
- **Markdown:**   marked.js — bundled locally in ui/marked.min.js

### Model selection (auto-detected at startup)
- High (≥20 GB RAM):   phi4-mini (GPU) + qwen3:8b (CPU)
- Medium (≥10 GB RAM): phi4-mini + qwen3:8b (both CPU)
- Low (≥5 GB RAM):     phi4-mini + qwen3:4b  ← i3/8GB target
- Minimal (<5 GB RAM): phi4-mini + gemma2:2b

### USB storage layout (when deployed to 32GB stick)
- OLLAMA_MODELS env var → points to models/ on the USB
- POCKETAI_DATA env var  → points to data/ on the USB (sessions, cache)
- All inference stays on the host CPU/RAM; USB is read-mostly after initial load

### Design principles
- Dual-model inference is completely hidden from the user
- UI targets 80-year-old non-technical users: large text, simple layout, voice-first
- All data (chat history, memories, cache) stored in data/ — never on host machine

### Infrastructure Cost Policy
- Target: keep costs minimal during development
- Prefer serverless / consumption-based pricing
- Document monthly cost estimate before provisioning any paid resource

---

## Repo Structure

*(To be defined during planning)*
