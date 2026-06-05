"""
main.py — FastAPI application for PocketAI.

Endpoints:
  GET  /                            → chat UI
  GET  /api/status                  → tier, models, cache stats
  POST /api/chat                    → SSE inference stream
  GET  /api/sessions                → recent sessions
  GET  /api/sessions/{id}/messages  → session message history
  POST /api/memories                → save a persistent memory
  GET  /api/memories                → list memories
  DELETE /api/memories/{id}         → delete a memory
  GET  /api/cache/stats             → cache statistics
  POST /api/upload                  → upload a file (PDF/docx/text/code)
  GET  /api/uploads                 → list active file uploads
  DELETE /api/uploads/{id}          → remove an uploaded file
  POST /api/shutdown                → gracefully stop the server
"""

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server import cache    as cache_mod
from server import memory   as mem_mod
from server import inference as inf_mod
from server import uploads  as uploads_mod
from server.config import detect_config, SERVER_PORT, USB_MODE

_launcher = None

app = FastAPI(title="PocketAI", docs_url=None, redoc_url=None)

_HERE = Path(__file__).resolve().parent.parent
_UI   = _HERE / "ui"

app.mount("/static", StaticFiles(directory=str(_UI)), name="static")

MODEL_TIMEOUT = 300


# ── Lifecycle ────────────────────────────────────────────────────

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


# ── UI ───────────────────────────────────────────────────────────

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


# ── Chat (SSE) ───────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(request: Request):
    """
    Body: { "query": "...", "session_id": "..." }
    SSE events:
      token_a / token_b   — live model tokens (thinking indicator)
      merge_token         — answer tokens shown in chat bubble
      done                — {"cached": bool, "session_id": "..."}
      error               — {"message": "..."}
    """
    body       = await request.json()
    query      = body.get("query", "").strip()
    session_id = body.get("session_id") or str(uuid.uuid4())

    if not query:
        return JSONResponse({"error": "Empty query"}, status_code=400)

    cfg = detect_config()

    async def event_stream():
        # Inject active file context into the query
        file_ctx = uploads_mod.build_file_context()
        effective_query = f"{file_ctx}\n\nUser question: {query}" if file_ctx else query

        # Cache check (uses original query as key, not the expanded one)
        cached = await cache_mod.get_cached(query, cfg.model_a, cfg.model_b)
        if cached:
            for chunk in inf_mod._stream_text(cached, chunk_size=8):
                yield f"data: {json.dumps({'type': 'merge_token', 'token': chunk})}\n\n"
                await asyncio.sleep(0.005)
            yield f"data: {json.dumps({'type':'done','cached':True,'session_id':session_id})}\n\n"
            await mem_mod.create_session(session_id)
            await mem_mod.add_message(session_id, "user",      query)
            await mem_mod.add_message(session_id, "assistant", cached)
            return

        # Fresh inference
        await mem_mod.create_session(session_id)
        await mem_mod.add_message(session_id, "user", query)

        history       = await mem_mod.get_session_messages(session_id)
        history       = [m for m in history if not (m["role"]=="user" and m["content"]==query)]
        system_prompt = await mem_mod.build_system_prompt(session_id)

        sse_queue:     asyncio.Queue = asyncio.Queue()
        merged_holder: list[str]     = []

        async def run_inference():
            try:
                ra, rb, merged = await inf_mod.run_dual(
                    effective_query, history, system_prompt, sse_queue
                )
                merged_holder.append(merged)
                await cache_mod.save_cache(query, cfg.model_a, cfg.model_b, merged)
                await mem_mod.add_message(session_id, "assistant", merged)

                msgs = await mem_mod.get_session_messages(session_id)
                if len(msgs) == 2:
                    title = await inf_mod.generate_title(query, cfg.model_a)
                    await mem_mod.update_session_title(session_id, title)
                if len(msgs) >= 4:
                    asyncio.create_task(_update_summary(session_id, msgs, cfg.model_a))
            except Exception as e:
                await sse_queue.put({"type": "error", "message": str(e)})

        asyncio.create_task(run_inference())

        while True:
            try:
                event = await asyncio.wait_for(sse_queue.get(), timeout=MODEL_TIMEOUT)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type':'error','message':'AI is taking too long. Is the model loaded?'})}\n\n"
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
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"},
    )


async def _update_summary(session_id: str, messages: list, model: str):
    try:
        summary = await inf_mod.generate_summary(messages, model)
        if summary:
            await mem_mod.update_session_summary(session_id, summary)
    except Exception:
        pass


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
    body = await request.json()
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


# ── Cache ─────────────────────────────────────────────────────────

@app.get("/api/cache/stats")
async def cache_stats_endpoint():
    return await cache_mod.cache_stats()


# ── File uploads ──────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Upload a file to add its text content as context for future queries.
    The file stays active until explicitly removed or the server restarts.
    """
    try:
        data = await file.read()
        result = uploads_mod.add_upload(file.filename, data)
        return {"ok": True, **result}
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Upload failed: {e}"}, status_code=500)


@app.get("/api/uploads")
async def list_uploads():
    return {"uploads": uploads_mod.list_uploads()}


@app.delete("/api/uploads/{upload_id}")
async def remove_upload(upload_id: int):
    ok = uploads_mod.remove_upload(upload_id)
    return {"ok": ok}


# ── Shutdown ──────────────────────────────────────────────────────

@app.post("/api/shutdown")
async def shutdown_server():
    """Gracefully stop PocketAI — stops llama-server and exits the process."""
    async def _do_shutdown():
        await asyncio.sleep(0.3)   # let the HTTP response send first
        if _launcher:
            await _launcher.stop()
        os._exit(0)

    asyncio.create_task(_do_shutdown())
    return {"ok": True, "message": "Shutting down..."}
