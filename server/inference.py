"""
inference.py — Parallel dual-model inference with live streaming.

Streaming approach (dual-model mode):
  1. Both models start simultaneously.
  2. Model A tokens stream live to the user as merge_token events → fast TTFT.
  3. Model B tokens go to the UI indicator only (token_b events).
  4. After both complete, novel sentences from B are appended if meaningfully different.
  5. One model failing is transparent — the other's answer is used alone.

Single-model mode (< 8 GB free RAM):
  Only model_a runs. Tokens stream directly as merge_token events.

API compatibility:
  Uses /v1/chat/completions (SSE) — works with Ollama and llama-server.
"""

import asyncio
import re
import json
from server.config import (
    LLM_HOST_A, LLM_HOST_B, detect_config,
    AVAILABLE_MODELS, text_model_ids, USB_MODE,
)

import httpx


def _host_for(model_id: str) -> str:
    """
    Return the API host for a given model id.

    Ollama mode: every model is served by the one Ollama instance.
    USB mode:    each model has its own llama-server on its own port.
    """
    if USB_MODE:
        port = AVAILABLE_MODELS.get(model_id, {}).get("port", 11434)
        return f"http://localhost:{port}"
    return LLM_HOST_A


def _tag_for(model_id: str) -> str:
    """Return the Ollama tag (or llama-server model name) for a model id."""
    return AVAILABLE_MODELS.get(model_id, {}).get("tag", model_id)

MODEL_TIMEOUT = 300   # 5 min covers cold-start model loading on first request

# Preamble patterns that models insert — stripped before returning response
_PREAMBLE_RE = re.compile(
    r"^(the assistant[^.]*?\.\s*|remembered?:\s*[^\n]*\n|"
    r"(certainly|of course|sure|great question|i understand)[,!.]?\s+)+",
    re.IGNORECASE,
)


# ── Streaming helper ─────────────────────────────────────────────

async def _stream_llm(host: str, model: str, messages: list, on_token=None, images: list = None) -> str:
    """
    Stream a chat completion via OpenAI-compatible /v1/chat/completions.
    Calls on_token(str) for each content chunk.
    Returns the full response text (stripped of preambles).
    Works with both Ollama and llama-server.

    If `images` (a list of base64 data URIs) is given, they are attached to
    the final user message using the multimodal content format so a vision
    model (e.g. Moondream) can see them.
    """
    full = ""
    url  = f"{host}/v1/chat/completions"

    payload_messages = messages
    if images:
        # Attach images to the last user message in multimodal format
        payload_messages = [dict(m) for m in messages]   # shallow copy
        for m in reversed(payload_messages):
            if m.get("role") == "user":
                text = m.get("content", "")
                content = [{"type": "text", "text": text}] if text else []
                for uri in images:
                    content.append({"type": "image_url", "image_url": {"url": uri}})
                m["content"] = content
                break

    # The model server returns 503 "loading model" while it reads the GGUF into
    # RAM (slow from a USB drive). Wait and retry rather than failing the query.
    import asyncio as _asyncio
    deadline = _asyncio.get_event_loop().time() + MODEL_TIMEOUT

    async with httpx.AsyncClient(timeout=MODEL_TIMEOUT) as client:
        while True:
            try:
                async with client.stream(
                    "POST", url,
                    json={"model": model, "messages": payload_messages, "stream": True},
                ) as resp:
                    if resp.status_code == 503:
                        # Model still loading — wait briefly and retry
                        await resp.aread()
                        if _asyncio.get_event_loop().time() >= deadline:
                            resp.raise_for_status()
                        await _asyncio.sleep(2.0)
                        continue
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                        except Exception:
                            continue
                        token = (
                            data.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content", "")
                        )
                        if token:
                            full += token
                            if on_token:
                                await on_token(token)
                break   # finished streaming successfully
            except httpx.HTTPStatusError:
                raise
            except (httpx.ConnectError, httpx.ReadError):
                # Server not accepting connections yet (still starting) — retry
                if _asyncio.get_event_loop().time() >= deadline:
                    raise
                await _asyncio.sleep(2.0)
                continue

    return _strip_preamble(full)


def _strip_preamble(text: str) -> str:
    """Remove model preambles like 'Certainly! ', 'The assistant notes...' etc."""
    return _PREAMBLE_RE.sub("", text).strip()


# ── Merge helpers ────────────────────────────────────────────────

def _words(text: str) -> set:
    stop = {
        "a","an","the","is","are","was","i","to","of","and","or","in",
        "it","this","that","for","on","with","be","at","by","as","but",
    }
    return {
        w.lower() for w in re.findall(r"\w+", text)
        if w.lower() not in stop and len(w) > 2
    }


def _overlap_ratio(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]


def _get_novel_sentences(primary: str, secondary: str, max_extra: int = 3) -> str:
    """
    Return sentences from secondary that add genuinely new content not in primary.
    Returns an empty string if secondary adds nothing useful.
    """
    primary_words = _words(primary)
    extra = []

    for sent in _sentences(secondary):
        sent_words = _words(sent)
        if not sent_words:
            continue
        novelty = len(sent_words - primary_words) / len(sent_words)
        if novelty > 0.55:
            extra.append(sent)

    return " ".join(extra[:max_extra])


# ── Main inference runner (model-selectable) ─────────────────────

async def run_inference(
    query:          str,
    history:        list,
    system_prompt:  str,
    sse_queue:      asyncio.Queue,
    selected_model: str = "all",
    images:         list = None,
) -> tuple:
    """
    Run inference and stream results to sse_queue.
    Returns (primary_response, secondary_response, merged_text).

    selected_model:
      "all"            — run all available text models, merge into one answer
      a model id       — run only that model (single, streams live)

    images:
      Optional list of base64 data URIs. When present, inference runs as a
      single vision model (caller routes to Moondream) and the images are
      attached to the user message.

    SSE event types:
      token_a     — primary model live token (thinking indicator)
      token_b     — secondary model live token (thinking indicator)
      merge_token — the answer token shown in the chat bubble
      done        — {"type":"done","cached":false}
    """
    messages = [{"role": "system", "content": system_prompt}] + history + [
        {"role": "user", "content": query}
    ]

    # Decide which models to run
    if images:
        # Vision query — single model only (caller has routed to a vision model)
        model_ids = [selected_model] if selected_model in AVAILABLE_MODELS else ["moondream"]
    elif selected_model == "all":
        model_ids = text_model_ids()
        # Keep only models that are actually configured
        model_ids = [m for m in model_ids if m in AVAILABLE_MODELS]
    elif selected_model in AVAILABLE_MODELS:
        model_ids = [selected_model]
    else:
        # Unknown selection — fall back to the first available model
        model_ids = [text_model_ids()[0]]

    # ── Single model: stream it live ─────────────────────────────
    if len(model_ids) == 1:
        mid = model_ids[0]

        async def on_tok(t):
            await sse_queue.put({"type": "merge_token", "token": t})

        try:
            resp = await _stream_llm(_host_for(mid), _tag_for(mid), messages, on_tok, images=images)
        except Exception as e:
            resp = ""
            err = f"Sorry, {AVAILABLE_MODELS[mid]['label']} is unavailable. ({e})"
            for chunk in _stream_text(err):
                await sse_queue.put({"type": "merge_token", "token": chunk})
                await asyncio.sleep(0.003)

        await sse_queue.put({"type": "done", "cached": False})
        return resp, "", resp

    # ── Multiple models: primary streams live, others run AFTER ──
    #
    # The other models run SEQUENTIALLY after the primary finishes, not
    # concurrently. On machines with little VRAM, running several models
    # at once causes severe thrashing (each swap reloads gigabytes). Running
    # them one at a time keeps each at full speed and the user sees the
    # primary answer immediately while the rest enhance it.
    primary_id = model_ids[0]
    other_ids  = model_ids[1:]

    primary_resp = ""
    primary_failed = False

    # Stream the primary model live to the chat bubble
    async def on_tok_a(t):
        nonlocal primary_resp
        primary_resp += t
        await sse_queue.put({"type": "merge_token", "token": t})

    try:
        await _stream_llm(_host_for(primary_id), _tag_for(primary_id), messages, on_tok_a)
    except Exception:
        primary_failed = True
        primary_resp = ""

    # Run each other model one at a time, appending novel info as it arrives
    merged = primary_resp
    good_others: list[str] = []

    for mid in other_ids:
        # Tell the UI another model is now working
        await sse_queue.put({"type": "token_b", "token": ""})
        try:
            other = await _stream_llm(_host_for(mid), _tag_for(mid), messages)
        except Exception:
            other = ""
        if not other or other.startswith("["):
            continue
        good_others.append(other)

        if primary_failed and merged == "":
            # Primary failed — use this model's answer as the base, stream it
            merged = other
            for chunk in _stream_text(other):
                await sse_queue.put({"type": "merge_token", "token": chunk})
                await asyncio.sleep(0.003)
        else:
            extra = _get_novel_sentences(merged, other)
            if extra:
                addition = "\n\n**Also worth noting:** " + extra
                merged = merged.rstrip() + addition
                for chunk in _stream_text(addition):
                    await sse_queue.put({"type": "merge_token", "token": chunk})
                    await asyncio.sleep(0.004)

    if primary_failed and not good_others:
        err = "Sorry, the AI models are unavailable. Is the engine running?"
        for chunk in _stream_text(err):
            await sse_queue.put({"type": "merge_token", "token": chunk})
            await asyncio.sleep(0.003)
        merged = err

    await sse_queue.put({"type": "done", "cached": False})
    secondary = good_others[0] if good_others else ""
    return primary_resp, secondary, merged


# Backwards-compatible alias
run_dual = run_inference


def _stream_text(text: str, chunk_size: int = 6):
    """Yield text in small chunks for smooth streaming."""
    for i in range(0, len(text), chunk_size):
        yield text[i: i + chunk_size]


# ── Session utilities ─────────────────────────────────────────────

async def generate_title(query: str, model: str) -> str:
    """Generate a short 4-5 word session title from the first message."""
    msgs = [
        {"role": "system", "content": "Reply with only a 4-5 word conversation title. No punctuation."},
        {"role": "user",   "content": query[:200]},
    ]
    try:
        return (await _stream_llm(LLM_HOST_A, model, msgs)).strip()[:60] or query[:40]
    except Exception:
        return query[:40]


async def generate_summary(messages: list, model: str) -> str:
    """Summarise a session for the rolling context preprompt (2-3 sentences)."""
    if not messages:
        return ""
    convo = "\n".join(
        f"{m['role'].upper()}: {m['content'][:300]}" for m in messages[-20:]
    )
    msgs = [
        {
            "role": "system",
            "content": (
                "Write a 2-3 sentence plain-English summary of this conversation. "
                "Topics discussed, any conclusions reached, user preferences shown. "
                "No preamble — start the summary immediately."
            ),
        },
        {"role": "user", "content": convo},
    ]
    try:
        return (await _stream_llm(LLM_HOST_A, model, msgs)).strip()
    except Exception:
        return ""
