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

from server import cache     as cache_mod
from server import memory    as mem_mod
from server import inference  as inf_mod
from server import uploads   as uploads_mod
from server import history   as history_mod
from server import knowledge as kb_mod
from server.config import (
    detect_config, SERVER_PORT, USB_MODE,
    AVAILABLE_MODELS, MODEL_ORDER,
)

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
    history_mod.init_history()   # rolling cross-session conversation memory
    kb_mod.ensure_index()        # build the knowledge-base search index if needed

    if USB_MODE:
        from server import launcher as launcher_mod
        global _launcher
        _launcher = launcher_mod.LlamaLauncher()
        launcher_mod.set_active_launcher(_launcher)
        cfg = detect_config()
        # Load the model in the BACKGROUND so the web UI opens immediately.
        # The first chat request calls ensure_model() and waits for it then.
        asyncio.create_task(_launcher.start(cfg))


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
        "history":      history_mod.history_stats(),
        "knowledge":    kb_mod.kb_stats(),
    }


# ── Models (for the UI toggle) ───────────────────────────────────

@app.get("/api/models")
async def list_models():
    """
    Return the available models for the UI toggle, plus the 'All models' option.
    Only models whose backend is actually reachable are marked available.
    """
    # Which Ollama tags are currently installed?
    installed_tags = set()
    if not USB_MODE:
        try:
            import httpx
            from server.config import LLM_HOST_A
            r = httpx.get(f"{LLM_HOST_A}/api/tags", timeout=3)
            if r.status_code == 200:
                installed_tags = {m["name"] for m in r.json().get("models", [])}
        except Exception:
            pass

    def is_available(tag: str) -> bool:
        if USB_MODE:
            # USB mode: assume the GGUF is present (launcher manages it)
            return True
        # Ollama mode: check if the tag (or its base) is installed
        base = tag.split(":")[0]
        return any(t == tag or t.startswith(base) for t in installed_tags)

    models = []
    for mid in MODEL_ORDER:
        m = AVAILABLE_MODELS[mid]
        models.append({
            "id":        mid,
            "label":     m["label"],
            "blurb":     m["blurb"],
            "vision":    m["vision"],
            "available": is_available(m["tag"]),
        })

    return {
        "default": "all",
        "models":  models,
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
    sel_model  = body.get("model") or "all"

    if not query:
        return JSONResponse({"error": "Empty query"}, status_code=400)

    cfg = detect_config()

    # Images attached? Route to the vision model (Moondream) and skip the cache.
    images = uploads_mod.get_image_data_uris()
    if images:
        vision_ids = [m for m in AVAILABLE_MODELS if AVAILABLE_MODELS[m]["vision"]]
        run_model  = vision_ids[0] if vision_ids else sel_model
    else:
        run_model  = sel_model

    # Cache key includes the model selection so different models cache separately
    cache_key_model = run_model

    async def event_stream():
        # Inject active TEXT file context into the query (images handled separately)
        file_ctx = uploads_mod.build_file_context()
        effective_query = f"{file_ctx}\n\nUser question: {query}" if file_ctx else query

        # Cache check — skipped entirely for image queries (every image is unique)
        if not images:
            cached = await cache_mod.get_cached(query, cache_key_model, "")
            if cached:
                for chunk in inf_mod._stream_text(cached, chunk_size=8):
                    yield f"data: {json.dumps({'type': 'merge_token', 'token': chunk})}\n\n"
                    await asyncio.sleep(0.005)
                yield f"data: {json.dumps({'type':'done','cached':True,'session_id':session_id})}\n\n"
                await mem_mod.create_session(session_id)
                await mem_mod.add_message(session_id, "user",      query)
                await mem_mod.add_message(session_id, "assistant", cached)
                history_mod.save_exchange(session_id, query, cached)
                return

        # Fresh inference
        await mem_mod.create_session(session_id)
        await mem_mod.add_message(session_id, "user", query)

        history       = await mem_mod.get_session_messages(session_id)
        history       = [m for m in history if not (m["role"]=="user" and m["content"]==query)]
        system_prompt = await mem_mod.build_system_prompt(session_id)

        # Knowledge-base retrieval (RAG): pull relevant passages for this query
        # and add them to the prompt. Skipped for image queries (vision model).
        if not images:
            try:
                kb_block = kb_mod.search_context(query)
                if kb_block:
                    system_prompt = system_prompt + "\n\n" + kb_block
            except Exception:
                pass   # retrieval is best-effort; never block a chat on it

        sse_queue:     asyncio.Queue = asyncio.Queue()
        merged_holder: list[str]     = []

        async def run_inference():
            try:
                # USB mode: make sure the needed model server(s) are running
                if USB_MODE and _launcher:
                    from server.config import text_model_ids
                    if images:
                        needed = [run_model]
                    else:
                        needed = text_model_ids() if run_model == "all" else [run_model]
                    await _launcher.ensure_models([m for m in needed if m in AVAILABLE_MODELS])

                ra, rb, merged = await inf_mod.run_inference(
                    effective_query, history, system_prompt, sse_queue,
                    selected_model=run_model, images=images,
                )
                merged_holder.append(merged)
                if not images:
                    await cache_mod.save_cache(query, cache_key_model, "", merged)
                await mem_mod.add_message(session_id, "assistant", merged)
                history_mod.save_exchange(session_id, query, merged)

                msgs = await mem_mod.get_session_messages(session_id)
                if len(msgs) == 2:
                    title = await inf_mod.generate_title(query, cfg.model_a)
                    await mem_mod.update_session_title(session_id, title)
                if len(msgs) >= 4:
                    asyncio.create_task(_update_summary(session_id, msgs, cfg.model_a))
            except Exception as e:
                await sse_queue.put({"type": "error", "message": str(e)})

        inference_task = asyncio.create_task(run_inference())

        try:
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
        finally:
            # If the client disconnected (pressed Stop) before we finished,
            # cancel the background inference so the model is freed immediately
            # for the next prompt instead of running to completion unseen.
            if not inference_task.done():
                inference_task.cancel()

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
