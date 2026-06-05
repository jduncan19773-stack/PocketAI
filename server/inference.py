"""
inference.py — Parallel dual-model inference with fast response merging.

Flow:
  1. Both models are queried simultaneously via asyncio.gather.
  2. Tokens from each stream to the SSE queue in real time.
  3. Once both finish, merge runs in < 10ms (no extra model call):
       a. >75% word overlap → return the longer one.
       b. Otherwise → use model_a as base, append novel sentences from model_b.
  4. Merged answer streams chunk-by-chunk to the SSE queue.
  5. One model failing does NOT kill the response — the other's answer is used.

Single-model mode (low RAM, e.g. 8GB i3):
  Only model_a is run. Tokens stream directly as merge_token events.
  No merging step. Significantly lower RAM usage.

API compatibility:
  Uses the OpenAI-compatible /v1/chat/completions endpoint (SSE).
  Works with both Ollama (>= 0.1.14) and llama-server (llama.cpp).
"""

import asyncio
import re
import json
from server.config import LLM_HOST_A, LLM_HOST_B, detect_config

import httpx

MODEL_TIMEOUT = 180   # seconds waiting for a streaming response


# ── Streaming helper ─────────────────────────────────────────────

async def _stream_llm(host: str, model: str, messages: list, on_token=None) -> str:
    """
    Stream a chat completion from the OpenAI-compatible /v1/chat/completions endpoint.
    Calls on_token(str) for each content chunk.
    Returns the full response text.
    Works identically with Ollama and llama-server.
    """
    full = ""
    url  = f"{host}/v1/chat/completions"

    async with httpx.AsyncClient(timeout=MODEL_TIMEOUT) as client:
        async with client.stream(
            "POST", url,
            json={"model": model, "messages": messages, "stream": True},
        ) as resp:
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

    return full


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


def _merge_fast(primary: str, secondary: str) -> str:
    """
    Deterministic merge — no model call, < 10ms.
    Keeps primary as the base answer. Appends sentences from secondary
    whose content is > 55% novel relative to primary.
    """
    primary_words = _words(primary)
    extra_sents   = []

    for sent in _sentences(secondary):
        sent_words = _words(sent)
        if not sent_words:
            continue
        novelty = len(sent_words - primary_words) / len(sent_words)
        if novelty > 0.55:
            extra_sents.append(sent)

    if not extra_sents:
        return primary

    extra_sents = extra_sents[:3]
    return primary.rstrip() + "\n\n**Additional context:** " + " ".join(extra_sents)


# ── Main dual/single-model runner ────────────────────────────────

async def run_dual(
    query:         str,
    history:       list,
    system_prompt: str,
    sse_queue:     asyncio.Queue,
) -> tuple:
    """
    Run inference. Returns (resp_a, resp_b, merged).

    In dual-model mode: both models run in parallel, responses are merged.
    In single-model mode (low RAM): only model_a runs; resp_b = "".

    SSE event types pushed to sse_queue:
      token_a     — model A token (dual mode only)
      token_b     — model B token (dual mode only)
      merge_token — merged/final answer token
      done        — terminal event {"type":"done","cached":false}
    """
    cfg          = detect_config()
    model_a      = cfg.model_a
    model_b      = cfg.model_b
    single_model = cfg.single_model

    messages = [{"role": "system", "content": system_prompt}] + history + [
        {"role": "user", "content": query}
    ]

    resp_a_ref: list[str] = []
    resp_b_ref: list[str] = []

    async def run_a():
        async def on_tok(t):
            if single_model:
                # In single-model mode stream directly as merge tokens so UI renders live
                await sse_queue.put({"type": "merge_token", "token": t})
            else:
                await sse_queue.put({"type": "token_a", "token": t})
        try:
            resp_a_ref.append(await _stream_llm(LLM_HOST_A, model_a, messages, on_tok))
        except Exception as e:
            resp_a_ref.append(f"[{model_a} unavailable: {e}]")

    async def run_b():
        async def on_tok(t):
            await sse_queue.put({"type": "token_b", "token": t})
        try:
            resp_b_ref.append(await _stream_llm(LLM_HOST_B, model_b, messages, on_tok))
        except Exception as e:
            resp_b_ref.append(f"[{model_b} unavailable: {e}]")

    if single_model:
        await run_a()
    else:
        await asyncio.gather(run_a(), run_b())

    resp_a = resp_a_ref[0] if resp_a_ref else ""
    resp_b = resp_b_ref[0] if resp_b_ref else ""

    # Handle failure cases
    a_failed = resp_a.startswith("[") and "unavailable" in resp_a
    b_failed = single_model or (resp_b.startswith("[") and "unavailable" in resp_b)

    if a_failed and b_failed:
        merged = "Sorry, the AI model is unavailable. Is Ollama running, or is llama-server started?"
        for chunk in _stream_text(merged):
            await sse_queue.put({"type": "merge_token", "token": chunk})
            await asyncio.sleep(0.004)
    elif a_failed:
        merged = resp_b
        for chunk in _stream_text(merged):
            await sse_queue.put({"type": "merge_token", "token": chunk})
            await asyncio.sleep(0.004)
    elif single_model:
        # Already streamed as merge_token above
        merged = resp_a
    else:
        # Both succeeded — merge, then stream merged
        overlap = _overlap_ratio(resp_a, resp_b)
        if overlap > 0.75:
            merged = resp_a if len(resp_a) >= len(resp_b) else resp_b
        else:
            merged = _merge_fast(resp_a, resp_b)

        for chunk in _stream_text(merged):
            await sse_queue.put({"type": "merge_token", "token": chunk})
            await asyncio.sleep(0.004)

    await sse_queue.put({"type": "done", "cached": False})
    return resp_a, resp_b, merged


def _stream_text(text: str, chunk_size: int = 6):
    """Yield text in small chunks to simulate natural streaming."""
    for i in range(0, len(text), chunk_size):
        yield text[i: i + chunk_size]


# ── Utility: generate short session title ────────────────────────

async def generate_title(query: str, model: str) -> str:
    """Ask the model to generate a ≤5-word session title from the first message."""
    msgs = [
        {"role": "system", "content": "Reply with only a 4-5 word conversation title. No punctuation."},
        {"role": "user",   "content": query[:200]},
    ]
    try:
        title = await _stream_llm(LLM_HOST_A, model, msgs)
        return title.strip()[:60] or query[:40]
    except Exception:
        return query[:40]


async def generate_summary(messages: list, model: str) -> str:
    """Summarise a session for the rolling memory preprompt (2-3 sentences max)."""
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
                "Cover: topics discussed, any conclusions reached, user preferences shown."
            ),
        },
        {"role": "user", "content": convo},
    ]
    try:
        return (await _stream_llm(LLM_HOST_A, model, msgs)).strip()
    except Exception:
        return ""
