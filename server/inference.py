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
from server.config import LLM_HOST_A, LLM_HOST_B, detect_config

import httpx

MODEL_TIMEOUT = 300   # 5 min covers cold-start model loading on first request

# Preamble patterns that models insert — stripped before returning response
_PREAMBLE_RE = re.compile(
    r"^(the assistant[^.]*?\.\s*|remembered?:\s*[^\n]*\n|"
    r"(certainly|of course|sure|great question|i understand)[,!.]?\s+)+",
    re.IGNORECASE,
)


# ── Streaming helper ─────────────────────────────────────────────

async def _stream_llm(host: str, model: str, messages: list, on_token=None) -> str:
    """
    Stream a chat completion via OpenAI-compatible /v1/chat/completions.
    Calls on_token(str) for each content chunk.
    Returns the full response text (stripped of preambles).
    Works with both Ollama and llama-server.
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


# ── Main dual/single-model runner ────────────────────────────────

async def run_dual(
    query:         str,
    history:       list,
    system_prompt: str,
    sse_queue:     asyncio.Queue,
) -> tuple:
    """
    Run inference and stream results to sse_queue.
    Returns (resp_a, resp_b, merged_text).

    SSE event types:
      token_a     — model A live token (shown in thinking indicator)
      token_b     — model B live token (shown in thinking indicator)
      merge_token — the answer token shown in the chat bubble
      done        — {"type":"done","cached":false}
    """
    cfg          = detect_config()
    model_a      = cfg.model_a
    model_b      = cfg.model_b
    single_model = cfg.single_model

    messages = [{"role": "system", "content": system_prompt}] + history + [
        {"role": "user", "content": query}
    ]

    resp_b_ref: list[str] = []
    a_failed = False

    if single_model:
        # ── Single-model: stream directly as merge_token ─────────
        async def on_tok_single(t):
            await sse_queue.put({"type": "merge_token", "token": t})

        try:
            resp_a = await _stream_llm(LLM_HOST_A, model_a, messages, on_tok_single)
        except Exception as e:
            resp_a = ""
            a_failed = True
            err = f"Sorry, the AI model is unavailable. Is Ollama running? ({e})"
            for chunk in _stream_text(err):
                await sse_queue.put({"type": "merge_token", "token": chunk})
                await asyncio.sleep(0.003)

        await sse_queue.put({"type": "done", "cached": False})
        return resp_a, "", resp_a

    # ── Dual-model: stream model_a live, enhance with model_b ────
    #
    # model_a tokens go straight to the chat bubble (merge_token)
    # → fast time-to-first-token for the user.
    #
    # model_b tokens go to the indicator only (token_b)
    # → user sees "Model B thinking" while reading model_a's answer.
    #
    # After both finish: if model_b has novel sentences, append them.

    resp_a = ""
    resp_a_failed = False

    # Start model_b in background immediately
    async def run_b():
        async def on_tok_b(t):
            await sse_queue.put({"type": "token_b", "token": t})
        try:
            resp_b_ref.append(await _stream_llm(LLM_HOST_B, model_b, messages, on_tok_b))
        except Exception:
            resp_b_ref.append("")

    b_task = asyncio.create_task(run_b())

    # Stream model_a live to the user
    async def on_tok_a(t):
        nonlocal resp_a
        resp_a += t
        await sse_queue.put({"type": "token_a", "token": t})
        await sse_queue.put({"type": "merge_token", "token": t})

    try:
        await _stream_llm(LLM_HOST_A, model_a, messages, on_tok_a)
    except Exception as e:
        resp_a_failed = True
        resp_a = ""

    # Wait for model_b
    await b_task
    resp_b = resp_b_ref[0] if resp_b_ref else ""
    b_failed = not resp_b or resp_b.startswith("[")

    if resp_a_failed and b_failed:
        # Both failed — error message
        err = "Sorry, both AI models are unavailable. Is Ollama running?"
        for chunk in _stream_text(err):
            await sse_queue.put({"type": "merge_token", "token": chunk})
            await asyncio.sleep(0.003)
        merged = err
    elif resp_a_failed:
        # model_a failed — stream model_b instead
        for chunk in _stream_text(resp_b):
            await sse_queue.put({"type": "merge_token", "token": chunk})
            await asyncio.sleep(0.003)
        merged = resp_b
    else:
        # model_a already streamed — check if model_b adds anything useful
        merged = resp_a
        if not b_failed:
            extra = _get_novel_sentences(resp_a, resp_b)
            if extra:
                addition = "\n\n**Also worth noting:** " + extra
                merged = resp_a.rstrip() + addition
                for chunk in _stream_text(addition):
                    await sse_queue.put({"type": "merge_token", "token": chunk})
                    await asyncio.sleep(0.004)

    await sse_queue.put({"type": "done", "cached": False})
    return resp_a, resp_b, merged


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
