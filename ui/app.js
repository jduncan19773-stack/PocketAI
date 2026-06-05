/* ─────────────────────────────────────────────────────────────
   app.js  —  PocketAI frontend
   Handles: chat, SSE streaming, voice input/output,
            session sidebar, memories, suggestions
   ───────────────────────────────────────────────────────────── */

// ── State ──────────────────────────────────────────────────────
let currentSessionId = null;
let isStreaming      = false;
let modelA           = "phi4-mini";
let modelB           = "";
let isSingleModel    = false;
let speakBack        = false;          // whether AI speaks its responses aloud
let recognition      = null;
const synth          = window.speechSynthesis;

// Marked.js — safe markdown with GitHub flavour
marked.setOptions({ breaks: true, gfm: true });

// ── Startup ───────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  await loadStatus();
  await Promise.all([loadSessions(), loadMemories()]);
  showEmpty();
  document.getElementById("query-input").focus();
});

// ── Status & model names ───────────────────────────────────────
async function loadStatus() {
  try {
    const r = await fetch("/api/status");
    const d = await r.json();
    modelA        = d.model_a;
    modelB        = d.model_b || "";
    isSingleModel = d.single_model || false;

    document.getElementById("model-a-name").textContent = modelA;
    document.getElementById("model-b-name").textContent = modelB;

    // In single-model mode, hide the second model badge
    if (isSingleModel) {
      const badgeB = document.getElementById("badge-b");
      if (badgeB) badgeB.style.display = "none";
    }

    const label = isSingleModel
      ? modelA
      : `${modelA} + ${modelB}`;
    setStatus("online", `Ready — ${label}`);
  } catch {
    setStatus("offline", "AI not connected — is it starting up?");
  }
}

function setStatus(state, text) {
  document.getElementById("status-indicator").className = `status-dot ${state}`;
  document.getElementById("status-text").textContent = text;
}

// ── Empty / welcome screen ─────────────────────────────────────
const SUGGESTIONS = [
  "Explain quantum computing in simple words",
  "Write a short poem about autumn",
  "What's the capital of Argentina?",
  "Help me write a short email",
  "How do I boil an egg perfectly?",
  "Tell me a fun fact about space",
  "What are the healthiest breakfast foods?",
  "Explain what a computer virus is",
];

function showEmpty() {
  const msgs = document.getElementById("messages");
  if (msgs.children.length > 0) return;

  const picks = [...SUGGESTIONS].sort(() => 0.5 - Math.random()).slice(0, 4);
  const pills = picks.map(s =>
    `<button class="suggestion" onclick="askSuggestion(this)">${s}</button>`
  ).join("");

  msgs.innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">◈</div>
      <div class="empty-title">Hello! I'm PocketAI</div>
      <div class="empty-sub">Your private AI — completely offline, all on your device. Type below or press 🎤 to speak.</div>
      <div class="suggestion-row">${pills}</div>
    </div>`;
}

function askSuggestion(btn) {
  document.getElementById("query-input").value = btn.textContent;
  sendMessage();
}

// ── Send message ───────────────────────────────────────────────
async function sendMessage() {
  const input = document.getElementById("query-input");
  const query = input.value.trim();
  if (!query || isStreaming) return;

  // Remove welcome screen
  const msgs  = document.getElementById("messages");
  const empty = msgs.querySelector(".empty-state");
  if (empty) empty.remove();

  appendMessage("user", query);
  input.value = "";
  autoResize(input);

  isStreaming = true;
  setSendDisabled(true);
  showThinking(true);

  // Create the AI response bubble (fills in as tokens arrive)
  const aiId     = "ai-" + Date.now();
  const aiBubble = document.createElement("div");
  aiBubble.className = "msg ai";
  aiBubble.innerHTML = `<div class="msg-label">PocketAI</div><div class="bubble" id="${aiId}"></div>`;
  msgs.appendChild(aiBubble);
  scrollBottom();

  let mergedText = "";
  let isMerging  = false;
  let wasCached  = false;

  try {
    const resp = await fetch("/api/chat", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, session_id: currentSessionId }),
    });

    if (!resp.ok) throw new Error(`Server error ${resp.status}`);

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data:")) continue;
        let evt;
        try { evt = JSON.parse(line.slice(5).trim()); } catch { continue; }

        if (evt.type === "token_a") {
          setBadge("badge-a", "active");

        } else if (evt.type === "token_b") {
          setBadge("badge-b", "active");

        } else if (evt.type === "merge_token") {
          if (!isMerging) {
            // First merge token — both models done, stop thinking animation
            showThinking(false);
            setBadge("badge-a", "done");
            if (!isSingleModel) setBadge("badge-b", "done");
            isMerging = true;
          }
          mergedText += evt.token;
          const el = document.getElementById(aiId);
          if (el) el.innerHTML = marked.parse(mergedText);
          scrollBottom();

        } else if (evt.type === "done") {
          if (evt.session_id) currentSessionId = evt.session_id;
          wasCached = evt.cached || false;
          loadSessions();   // refresh sidebar

        } else if (evt.type === "error") {
          showThinking(false);
          const el = document.getElementById(aiId);
          if (el) el.innerHTML = `<span class="err-msg">⚠ ${escapeHtml(evt.message)}</span>`;
        }
      }
    }
  } catch (e) {
    showThinking(false);
    const el = document.getElementById(aiId);
    if (el) el.innerHTML = `<span class="err-msg">⚠ Could not reach PocketAI. Is the server still running?</span>`;
  }

  showThinking(false);
  setSendDisabled(false);
  isStreaming = false;

  if (wasCached) {
    const el = document.getElementById(aiId);
    if (el) el.innerHTML += `<div><span class="cached-badge">⚡ Instant — from memory</span></div>`;
  }

  // Speak the response if voice-back is on
  if (speakBack && mergedText) speak(mergedText);

  scrollBottom();
}

// ── Voice input ────────────────────────────────────────────────
function toggleVoice() {
  if (recognition && recognition.running) { stopVoice(); return; }
  startVoice();
}

function startVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    alert("Voice input is not supported in this browser.\nPlease use Chrome or Edge.");
    return;
  }

  recognition = new SR();
  recognition.lang           = "en-US";
  recognition.interimResults = true;
  recognition.continuous     = false;
  recognition.running        = true;

  document.getElementById("voice-btn").classList.add("recording");
  document.getElementById("voice-banner").classList.remove("hidden");
  document.getElementById("voice-status-text").textContent = "Listening… speak your question";

  recognition.onresult = (e) => {
    const transcript = Array.from(e.results).map(r => r[0].transcript).join("");
    document.getElementById("query-input").value = transcript;
    document.getElementById("voice-status-text").textContent = `"${transcript}"`;
    if (e.results[e.results.length - 1].isFinal) {
      stopVoice();
      sendMessage();
    }
  };

  recognition.onerror = (e) => {
    const msgs = {
      "not-allowed":  "Microphone blocked — please allow microphone access in your browser.",
      "no-speech":    "No speech detected. Please try again.",
      "network":      "Network error with voice recognition.",
      "aborted":      "Voice input was cancelled.",
    };
    document.getElementById("voice-status-text").textContent =
      msgs[e.error] || `Voice error: ${e.error}`;
    setTimeout(stopVoice, 2000);
  };

  recognition.onend = () => stopVoice();
  recognition.start();
}

function stopVoice() {
  if (recognition) { recognition.stop(); recognition.running = false; }
  document.getElementById("voice-btn").classList.remove("recording");
  document.getElementById("voice-banner").classList.add("hidden");
}

// ── Voice output ───────────────────────────────────────────────
function toggleSpeakBack() {
  speakBack = !speakBack;
  const btn = document.getElementById("speak-btn");
  if (speakBack) {
    btn.textContent = "🔊";
    btn.classList.add("active");
    btn.title = "Spoken responses ON — click to turn off";
  } else {
    btn.textContent = "🔇";
    btn.classList.remove("active");
    btn.title = "Spoken responses OFF — click to turn on";
    synth && synth.cancel();   // stop any currently speaking response
  }
}

function speak(text) {
  if (!synth) return;
  synth.cancel();
  // Strip markdown symbols before speaking
  const plain = text
    .replace(/#{1,6}\s/g, "")
    .replace(/[*_`~\[\]>]/g, "")
    .replace(/\n+/g, " ")
    .substring(0, 800);

  const utt   = new SpeechSynthesisUtterance(plain);
  utt.rate    = 0.92;
  utt.pitch   = 1.0;

  // Prefer a natural-sounding voice if available
  const voices   = synth.getVoices();
  const preferred = voices.find(v =>
    v.name.includes("Google") || v.name.includes("Natural") || v.name.includes("Premium")
  );
  if (preferred) utt.voice = preferred;

  synth.speak(utt);
}

// ── Helpers ────────────────────────────────────────────────────
function appendMessage(role, text) {
  const msgs = document.getElementById("messages");
  const div  = document.createElement("div");
  div.className = `msg ${role}`;
  const label = role === "user" ? "You" : "PocketAI";
  div.innerHTML = `<div class="msg-label">${label}</div><div class="bubble">${escapeHtml(text)}</div>`;
  msgs.appendChild(div);
  scrollBottom();
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function scrollBottom() {
  const msgs = document.getElementById("messages");
  requestAnimationFrame(() => { msgs.scrollTop = msgs.scrollHeight; });
}

function showThinking(show) {
  const el = document.getElementById("thinking");
  if (show) {
    el.classList.remove("hidden");
    setBadge("badge-a", "active");
    if (!isSingleModel) setBadge("badge-b", "active");
    const lbl = document.getElementById("thinking-label");
    if (lbl) lbl.textContent = isSingleModel ? "Thinking…" : "Both models thinking…";
  } else {
    el.classList.add("hidden");
    setBadge("badge-a", "");
    setBadge("badge-b", "");
  }
}

function setBadge(id, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = "model-badge" + (cls ? " " + cls : "");
}

function setSendDisabled(disabled) {
  document.getElementById("send-btn").disabled = disabled;
}

function handleKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

// ── New chat ───────────────────────────────────────────────────
function newChat() {
  currentSessionId = null;
  isStreaming       = false;
  synth && synth.cancel();
  document.getElementById("messages").innerHTML = "";
  showEmpty();
  document.getElementById("query-input").focus();
  document.querySelectorAll(".session-item").forEach(el => el.classList.remove("active"));
}

// ── Sessions sidebar ───────────────────────────────────────────
async function loadSessions() {
  try {
    const r   = await fetch("/api/sessions");
    const d   = await r.json();
    const list = document.getElementById("sessions-list");

    if (!d.sessions.length) {
      list.innerHTML = `<div style="font-size:13px;color:var(--text-muted);padding:6px 10px">No conversations yet</div>`;
      return;
    }
    list.innerHTML = d.sessions.map(s => `
      <div class="session-item${s.id === currentSessionId ? " active" : ""}"
           onclick="loadSession('${s.id}')"
           title="${escapeHtml(s.title || "Conversation")}">
        ${escapeHtml((s.title || "Conversation").substring(0, 36))}
      </div>`).join("");
  } catch {}
}

async function loadSession(id) {
  try {
    currentSessionId = id;
    const r = await fetch(`/api/sessions/${id}/messages`);
    const d = await r.json();
    const msgs = document.getElementById("messages");
    msgs.innerHTML = "";

    for (const m of d.messages) {
      if (m.role === "user") {
        appendMessage("user", m.content);
      } else {
        const div = document.createElement("div");
        div.className = "msg ai";
        div.innerHTML = `<div class="msg-label">PocketAI</div><div class="bubble">${marked.parse(m.content)}</div>`;
        msgs.appendChild(div);
      }
    }
    scrollBottom();

    // Mark the active session in the sidebar
    document.querySelectorAll(".session-item").forEach(el => {
      el.classList.toggle("active", el.getAttribute("onclick")?.includes(id));
    });
  } catch {}
}

// ── Memories ───────────────────────────────────────────────────
async function loadMemories() {
  try {
    const r    = await fetch("/api/memories");
    const d    = await r.json();
    const list = document.getElementById("memories-list");

    if (!d.memories.length) {
      list.innerHTML = `<div style="font-size:13px;color:var(--text-muted);padding:6px 10px">Nothing saved yet</div>`;
      return;
    }
    list.innerHTML = d.memories.map(m => `
      <div class="memory-item">
        <span title="${escapeHtml(m.content)}">${escapeHtml(m.content.substring(0, 55))}${m.content.length > 55 ? "…" : ""}</span>
        <button class="memory-del" onclick="deleteMemory(${m.id})" title="Remove this">✕</button>
      </div>`).join("");
  } catch {}
}

function showAddMemory() {
  document.getElementById("memory-modal").classList.remove("hidden");
  setTimeout(() => document.getElementById("memory-input").focus(), 50);
}

function closeAddMemory() {
  document.getElementById("memory-modal").classList.add("hidden");
  document.getElementById("memory-input").value = "";
}

async function saveMemory() {
  const content = document.getElementById("memory-input").value.trim();
  if (!content) return;
  await fetch("/api/memories", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ content }),
  });
  closeAddMemory();
  loadMemories();
}

async function deleteMemory(id) {
  await fetch(`/api/memories/${id}`, { method: "DELETE" });
  loadMemories();
}

// ── Mobile sidebar ─────────────────────────────────────────────
function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("open");
}

// Close sidebar when clicking outside it on mobile
document.addEventListener("click", (e) => {
  const sidebar = document.getElementById("sidebar");
  if (
    window.innerWidth <= 700 &&
    sidebar.classList.contains("open") &&
    !sidebar.contains(e.target) &&
    !e.target.closest(".menu-btn")
  ) {
    sidebar.classList.remove("open");
  }
});
