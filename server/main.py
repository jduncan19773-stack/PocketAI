"""
main.py — FastAPI application for PocketAI.

Endpoints:
  GET  /                          → serves the chat UI (index.html)
  GET  /api/status                → hardware tier, model names, cache stats
  POST /api/chat                  → SSE stream: dual/single-model inference
  GET  /api/sessions              → list recent sessions (title + timestamp)
  GET  /api/sessions/{id}/messages → full message history for a session
  POST /api/memories              → save a persistent user memory
  GET  /api/memories              → list all memories
  DELETE /api/memories/{id}       → delete a memory
  GET  /api/cache/stats           → cache hit/miss statistics

Startup behaviour:
  If POCKETAI_MODE=usb, starts llama-server.exe processes from the USB before
  accepting requests. In Ollama mode (default) assumes Ollama is already running.

Session summaries:
  After every AI response that brings a session to 4+ messages, a background task
  generates a short summary and saves it. These summaries are prepended as context
  in every future session (the "context from recent conversations" feature).
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server import cache as cache_mod
from server import memory as mem_mod
from server import inference as inf_mod
from server.config import detect_config, SERVER_PORT, USB_MODE

# Lazy import — only used in USB mode
_launcher = None

app = FastAPI(title="PocketAI", docs_url=None, redoc_url=None)

_HERE = Path(__file__).parent.parent
_UI   = _HERE / "ui"

app.mount("/static", StaticFiles(directory=str(_UI)), name="static")

# Seconds to wait for a model token before giving up
MODEL_TIMEOUT = 180


# ── Startup / shutdown ───────────────────────────────────────────

@app.on_event("startup")
async def startup():
    await cache_mod.init_cache()
    await mem_mod.init_memory()

    if USB_MODE:
        from server import launcher as launcher_mod
        global _launcher
        _launcher = launcher_mod.LlamaLauncher()
        cfg = detect_config()
        await _launcher.start(cfg)


@app.on_event("shutdown")
async def shutdown():
    if _launcher:
        await _launcher.stop()


# ── Static UI ────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return (_UI / "index.html").read_text(encoding="utf-8")


# ── Status ───────────────────────────────────────────────────────

@app.get("/api/status")
async def status():
    cfg   = detect_config()
    stats = await cache_mod.cache_stats()
    return {
        "tier":         cfg.tier,
        "model_a":      cfg.model_a,
        "model_b":      cfg.model_b,
        "single_model": cfg.single_model,
        "description":  cfg.description,
        "cache":        stats,
    }


# ── Chat (SSE streaming) ─────────────────────────────────────────

@app.post("/api/chat")
async def chat(request: Request):
    """
    Accepts:  { "query": "...", "session_id": "..." }
    Returns:  Server-Sent Events stream

    Event types:
      {"type": "token_a",     "token": "..."}   — model A live token
      {"type": "token_b",     "token": "..."}   — model B live token
      {"type": "merge_token", "token": "..."}   — merged/final answer token
      {"type": "done",        "cached": bool, "session_id": "..."}
      {"type": "error",       "message": "..."}
    """
    body       = await request.json()
    query      = body.get("query", "").strip()
    session_id = body.get("session_id") or str(uuid.uuid4())

    if not query:
        return JSONResponse({"error": "Empty query"}, status_code=400)

    cfg = detect_config()

    async def event_stream():
        # ── Cache hit — stream cached answer directly ────────────
        cached = await cache_mod.get_cached(query, cfg.model_a, cfg.model_b)
        if cached:
            for chunk in inf_mod._stream_text(cached, chunk_size=8):
                yield f"data: {json.dumps({'type': 'merge_token', 'token': chunk})}\n\n"
                await asyncio.sleep(0.005)
            yield f"data: {json.dumps({'type': 'done', 'cached': True, 'session_id': session_id})}\n\n"

            await mem_mod.create_session(session_id)
            await mem_mod.add_message(session_id, "user",      query)
            await mem_mod.add_message(session_id, "assistant", cached)
            return

        # ── Cache miss — run inference ───────────────────────────
        await mem_mod.create_session(session_id)
        await mem_mod.add_message(session_id, "user", query)

        # Build history (exclude the message we just added — it's in `query`)
        history       = await mem_mod.get_session_messages(session_id)
        history       = [m for m in history if not (m["role"] == "user" and m["content"] == query)]
        system_prompt = await mem_mod.build_system_prompt(session_id)

        sse_queue:     asyncio.Queue = asyncio.Queue()
        merged_holder: list[str]     = []

        async def run_inference():
            try:
                ra, rb, merged = await inf_mod.run_dual(
                    query, history, system_prompt, sse_queue
                )
                merged_holder.append(merged)

                # Save response to cache and session
                await cache_mod.save_cache(query, cfg.model_a, cfg.model_b, merged)
                await mem_mod.add_message(session_id, "assistant", merged)

                # Generate session title after first exchange
                msgs = await mem_mod.get_session_messages(session_id)
                if len(msgs) == 2:
                    title = await inf_mod.generate_title(query, cfg.model_a)
                    await mem_mod.update_session_title(session_id, title)

                # Generate session summary once the session has ≥ 4 messages
                # (2 user + 2 assistant = a meaningful conversation worth summarising).
                # Runs as a background task so it doesn't block the response.
                if len(msgs) >= 4:
                    asyncio.create_task(
                        _update_summary(session_id, msgs, cfg.model_a)
                    )

            except Exception as e:
                await sse_queue.put({"type": "error", "message": str(e)})

        asyncio.create_task(run_inference())

        # Forward SSE queue → HTTP response
        while True:
            try:
                event = await asyncio.wait_for(sse_queue.get(), timeout=MODEL_TIMEOUT)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'error', 'message': 'The AI is taking too long. Is the model loaded?'})}\n\n"
                break

            if event.get("type") == "done":
                yield f"data: {json.dumps({**event, 'session_id': session_id})}\n\n"
                break
            elif event.get("type") == "error":
                yield f"data: {json.dumps(event)}\n\n"
                break
            else:
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _update_summary(session_id: str, messages: list, model: str):
    """Background task: generate and save a session summary."""
    try:
        summary = await inf_mod.generate_summary(messages, model)
        if summary:
            await mem_mod.update_session_summary(session_id, summary)
    except Exception:
        pass  # Summary failure is non-fatal


# ── Sessions ─────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions():
    return {"sessions": await mem_mod.get_recent_sessions(30)}


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    return {"messages": await mem_mod.get_session_messages(session_id)}


# ── Memories ─────────────────────────────────────────────────────

@app.post("/api/memories")
async def add_memory(request: Request):
    body    = await request.json()
    content = body.get("content", "").strip()
    if content:
        await mem_mod.save_memory(content)
    return {"ok": True}


@app.get("/api/memories")
async def list_memories():
    return {"memories": await mem_mod.get_memories()}


@app.delete("/api/memories/{memory_id}")
async def del_memory(memory_id: int):
    await mem_mod.delete_memory(memory_id)
    return {"ok": True}


# ── Cache stats ───────────────────────────────────────────────────

@app.get("/api/cache/stats")
async def cache_stats_endpoint():
    return await cache_mod.cache_stats()
