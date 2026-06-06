/* ──────────────────────────────────────────────────────────────
   app.js  —  PocketAI frontend
   ────────────────────────────────────────────────────────────── */

marked.setOptions({ breaks: true, gfm: true });

// ── State ──────────────────────────────────────────────────────
let sessionId     = null;   // current session UUID
let isStreaming   = false;
let speakBack     = false;
let recognition   = null;
let isSingleModel = true;
let modelA        = "phi4-mini";
let modelB        = "";
let selectedModel = "all";   // current model toggle selection
let availableModels = [];    // populated from /api/models
const synth       = window.speechSynthesis;

// ── Init ───────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  await loadStatus();
  await loadModels();
  await Promise.all([loadSessions(), loadMemories(), loadUploads()]);
  showWelcome();
  document.getElementById("query-input").focus();
});

// ── Status ─────────────────────────────────────────────────────
async function loadStatus() {
  try {
    const d = await api("/api/status");
    modelA        = d.model_a;
    modelB        = d.model_b || "";
    isSingleModel = d.single_model || false;
    setStatus("online", "AI ready");
  } catch {
    setStatus("offline", "Connecting...");
    setTimeout(loadStatus, 3000);
  }
}

function setStatus(cls, text) {
  document.getElementById("status-dot").className  = `status-dot ${cls}`;
  document.getElementById("status-text").textContent = text;
}

// ── Model picker ───────────────────────────────────────────────
async function loadModels() {
  try {
    const d = await api("/api/models");
    availableModels = d.models || [];
    selectedModel   = localStorage.getItem("pocketai_model") || d.default || "all";
    renderModelMenu();
    updateModelLabel();
  } catch {
    availableModels = [];
  }
}

function renderModelMenu() {
  const menu = document.getElementById("model-menu");
  const avail = availableModels.filter(m => m.available);

  // "All models" option at top
  let html = `<div class="model-menu-header">Smart Mode</div>`;
  html += modelOptionHTML({
    id: "all",
    label: "All Models",
    blurb: "Searches across every model and combines the best answer",
    available: avail.length > 0,
    vision: false,
  });

  html += `<div class="model-menu-divider"></div>`;
  html += `<div class="model-menu-header">Single Model</div>`;

  for (const m of availableModels) {
    html += modelOptionHTML(m);
  }
  menu.innerHTML = html;
}

function modelOptionHTML(m) {
  const sel = m.id === selectedModel ? " selected" : "";
  const dis = m.available ? "" : " disabled";
  let tag = "";
  if (m.vision)        tag = `<span class="mo-tag vision">Images</span>`;
  if (!m.available)    tag = `<span class="mo-tag unavailable">Not installed</span>`;
  return `
    <button class="model-option${sel}"${dis} onclick="selectModel('${m.id}')">
      <span class="mo-radio"></span>
      <span class="mo-text">
        <span class="mo-label">${esc(m.label)} ${tag}</span>
        <span class="mo-blurb">${esc(m.blurb)}</span>
      </span>
    </button>`;
}

function selectModel(id) {
  selectedModel = id;
  localStorage.setItem("pocketai_model", id);
  renderModelMenu();
  updateModelLabel();
  closeModelMenu();
}

function updateModelLabel() {
  const label = document.getElementById("model-picker-label");
  const hint  = document.getElementById("hint-model");
  if (selectedModel === "all") {
    label.textContent = "All Models";
    if (hint) hint.textContent = "All Models";
  } else {
    const m = availableModels.find(x => x.id === selectedModel);
    const name = m ? m.label : selectedModel;
    label.textContent = name;
    if (hint) hint.textContent = name;
  }
}

function toggleModelMenu(e) {
  e.stopPropagation();
  document.getElementById("model-menu").classList.toggle("hidden");
}
function closeModelMenu() {
  document.getElementById("model-menu").classList.add("hidden");
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".model-picker")) closeModelMenu();
});

// ── Welcome screen ─────────────────────────────────────────────
const SUGGESTIONS = [
  "Explain quantum computing simply",
  "Help me write a professional email",
  "What are the healthiest breakfast foods?",
  "Write a short poem about autumn",
  "How do I improve my sleep?",
  "What is the Eiffel Tower made of?",
  "Give me a chocolate chip cookie recipe",
  "What causes a rainbow?",
];

function showWelcome() {
  const msgs = document.getElementById("messages");
  if (msgs.children.length > 0) return;

  const picks = [...SUGGESTIONS].sort(() => .5 - Math.random()).slice(0, 4);
  msgs.innerHTML = `
    <div class="empty-state">
      <div class="empty-logo">P</div>
      <div class="empty-title">Hello, I'm PocketAI</div>
      <div class="empty-sub">Your private AI assistant — running completely offline on this device. Everything stays here, nothing is sent anywhere.</div>
      <div class="suggestion-grid">
        ${picks.map(s => `<button class="suggestion" onclick="useSuggestion(this)">${s}</button>`).join("")}
      </div>
    </div>`;
}

function useSuggestion(btn) {
  document.getElementById("query-input").value = btn.textContent;
  sendMessage();
}

// ── Send ────────────────────────────────────────────────────────
async function sendMessage() {
  const input = document.getElementById("query-input");
  const query = input.value.trim();
  if (!query || isStreaming) return;

  const msgs = document.getElementById("messages");
  msgs.querySelector(".empty-state")?.remove();

  appendUserMsg(query);
  input.value = "";
  autoResize(input);
  isStreaming = true;
  setSendDisabled(true);
  showThinking(true);

  // Create AI bubble
  const aiId    = "ai-" + Date.now();
  const bubble  = document.createElement("div");
  bubble.className = "msg ai";
  bubble.innerHTML = `
    <div class="ai-avatar">P</div>
    <div class="ai-content">
      <div class="bubble" id="${aiId}"></div>
    </div>`;
  msgs.appendChild(bubble);
  scrollBottom();

  let merged = "", merging = false, cached = false;

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, session_id: sessionId, model: selectedModel }),
    });
    if (!resp.ok) throw new Error(`Server error ${resp.status}`);

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      for (const line of decoder.decode(value, { stream: true }).split("\n")) {
        if (!line.startsWith("data:")) continue;
        let e;
        try { e = JSON.parse(line.slice(5).trim()); } catch { continue; }

        if (e.type === "token_a") {
          setBadge("badge-a", "active");
        } else if (e.type === "token_b") {
          setBadge("badge-b", "active");
        } else if (e.type === "merge_token") {
          if (!merging) {
            showThinking(false);
            setBadge("badge-a", "done");
            merging = true;
          }
          merged += e.token;
          const el = document.getElementById(aiId);
          if (el) { el.innerHTML = marked.parse(merged); scrollBottom(); }
        } else if (e.type === "done") {
          sessionId = e.session_id || sessionId;
          cached    = e.cached || false;
          setBadge("badge-b", "done");
          loadSessions();
        } else if (e.type === "error") {
          showThinking(false);
          const el = document.getElementById(aiId);
          if (el) el.innerHTML = `<span style="color:var(--red)">&#9888; ${esc(e.message)}</span>`;
        }
      }
    }
  } catch (err) {
    showThinking(false);
    const el = document.getElementById(aiId);
    if (el) el.innerHTML = `<span style="color:var(--red)">&#9888; Connection error — is PocketAI still running?</span>`;
  }

  showThinking(false);
  setSendDisabled(false);
  isStreaming = false;

  if (cached) {
    const el = document.getElementById(aiId);
    if (el) {
      const meta = document.createElement("div");
      meta.className = "msg-meta";
      meta.innerHTML = `<span class="cached-pill">&#9889; Instant recall</span>`;
      el.closest(".ai-content").appendChild(meta);
    }
  }

  if (speakBack && merged) speak(merged);
  scrollBottom();
}

// ── File uploads ────────────────────────────────────────────────
async function handleFileUpload(input) {
  const files = Array.from(input.files);
  input.value = "";   // allow re-upload of same file
  for (const file of files) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd });
      const d = await r.json();
      if (!d.ok) {
        alert(`Could not read ${file.name}:\n${d.error}`);
      }
    } catch {
      alert(`Upload failed for ${file.name}`);
    }
  }
  loadUploads();
}

async function loadUploads() {
  try {
    const d    = await api("/api/uploads");
    const bar  = document.getElementById("attachments-bar");
    const chips = document.getElementById("attachment-chips");

    if (!d.uploads.length) { bar.classList.add("hidden"); return; }

    bar.classList.remove("hidden");
    chips.innerHTML = d.uploads.map(u => `
      <div class="attachment-chip">
        <span class="chip-name" title="${esc(u.filename)}">${esc(u.filename)}</span>
        <span class="chip-size">${fmtK(u.chars)}</span>
        <button class="chip-remove" onclick="removeUpload(${u.id})" title="Remove">&#x2715;</button>
      </div>`).join("");
  } catch {}
}

async function removeUpload(id) {
  await fetch(`/api/uploads/${id}`, { method: "DELETE" });
  loadUploads();
}

function fmtK(n) {
  return n >= 1000 ? `${(n/1000).toFixed(1)}k chars` : `${n} chars`;
}

// ── Voice input ─────────────────────────────────────────────────
function toggleVoice() {
  if (recognition?.running) { stopVoice(); return; }
  startVoice();
}

function startVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { alert("Voice input requires Chrome or Edge."); return; }

  recognition = new SR();
  recognition.lang = "en-US";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.running = true;

  document.getElementById("mic-btn").classList.add("recording");
  document.getElementById("voice-banner").classList.remove("hidden");
  setVoiceStatus("Listening... speak your question");

  recognition.onresult = (e) => {
    const t = Array.from(e.results).map(r => r[0].transcript).join("");
    document.getElementById("query-input").value = t;
    setVoiceStatus(`"${t}"`);
    if (e.results[e.results.length - 1].isFinal) { stopVoice(); sendMessage(); }
  };

  recognition.onerror = (e) => {
    const msgs = {
      "not-allowed": "Microphone blocked — please allow microphone access.",
      "no-speech":   "No speech detected. Try again.",
      "network":     "Network error with voice recognition.",
    };
    setVoiceStatus(msgs[e.error] || `Error: ${e.error}`);
    setTimeout(stopVoice, 2000);
  };

  recognition.onend = stopVoice;
  recognition.start();
}

function stopVoice() {
  recognition?.stop();
  if (recognition) recognition.running = false;
  document.getElementById("mic-btn").classList.remove("recording");
  document.getElementById("voice-banner").classList.add("hidden");
}

function setVoiceStatus(t) { document.getElementById("voice-status").textContent = t; }

// ── Voice output ────────────────────────────────────────────────
function toggleSpeakBack() {
  speakBack = !speakBack;
  const btn  = document.getElementById("speak-btn");
  const icon = document.getElementById("speak-icon");

  if (speakBack) {
    btn.classList.add("active");
    btn.title = "Spoken responses ON — click to turn off";
    // Switch to volume-on icon
    icon.innerHTML = `
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
      <path d="M15.54 8.46a5 5 0 010 7.07"/>
      <path d="M19.07 4.93a10 10 0 010 14.14"/>`;
  } else {
    btn.classList.remove("active");
    btn.title = "Spoken responses OFF — click to turn on";
    // Mute icon
    icon.innerHTML = `
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
      <line x1="23" y1="9" x2="17" y2="15"/>
      <line x1="17" y1="9" x2="23" y2="15"/>`;
    synth?.cancel();
  }
}

function speak(text) {
  if (!synth) return;
  synth.cancel();
  const plain = text.replace(/#{1,6}\s/g,"").replace(/[*_`~\[\]>]/g,"").replace(/\n+/g," ").substring(0, 800);
  const utt = new SpeechSynthesisUtterance(plain);
  utt.rate = 0.92;
  const best = synth.getVoices().find(v => v.name.includes("Google") || v.name.includes("Natural") || v.name.includes("Premium"));
  if (best) utt.voice = best;
  synth.speak(utt);
}

// ── Shutdown ────────────────────────────────────────────────────
function confirmShutdown() { document.getElementById("shutdown-modal").classList.remove("hidden"); }
function closeShutdown()   { document.getElementById("shutdown-modal").classList.add("hidden"); }

async function doShutdown() {
  closeShutdown();
  setStatus("loading", "Stopping...");
  try { await fetch("/api/shutdown", { method: "POST" }); } catch {}
  setTimeout(() => {
    document.body.innerHTML = `
      <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;gap:16px;font-family:system-ui;color:#94a3b8;">
        <div style="font-size:48px">&#x2713;</div>
        <div style="font-size:20px;color:#e2e8f0;font-weight:600">PocketAI stopped.</div>
        <div style="font-size:15px">You can close this window.</div>
      </div>`;
  }, 800);
}

// ── New chat ────────────────────────────────────────────────────
function newChat() {
  sessionId   = null;
  isStreaming  = false;
  synth?.cancel();
  document.getElementById("messages").innerHTML = "";
  showWelcome();
  document.getElementById("query-input").focus();
  document.querySelectorAll(".session-item").forEach(el => el.classList.remove("active"));
  if (window.innerWidth <= 680) document.getElementById("sidebar").classList.remove("open");
}

// ── Sessions ────────────────────────────────────────────────────
async function loadSessions() {
  try {
    const d    = await api("/api/sessions");
    const list = document.getElementById("sessions-list");
    if (!d.sessions.length) {
      list.innerHTML = `<div style="font-size:13px;color:var(--text-muted);padding:6px 4px">No conversations yet</div>`;
      return;
    }
    list.innerHTML = d.sessions.map(s => `
      <div class="session-item${s.id === sessionId ? " active" : ""}"
           onclick="loadSession('${s.id}')"
           title="${esc(s.title || "Conversation")}">
        ${esc((s.title || "Conversation").substring(0, 36))}
      </div>`).join("");
  } catch {}
}

async function loadSession(id) {
  try {
    sessionId = id;
    const d   = await api(`/api/sessions/${id}/messages`);
    const msgs = document.getElementById("messages");
    msgs.innerHTML = "";

    for (const m of d.messages) {
      if (m.role === "user") {
        appendUserMsg(m.content);
      } else {
        const div = document.createElement("div");
        div.className = "msg ai";
        div.innerHTML = `
          <div class="ai-avatar">P</div>
          <div class="ai-content">
            <div class="bubble">${marked.parse(m.content)}</div>
          </div>`;
        msgs.appendChild(div);
      }
    }
    scrollBottom();
    document.querySelectorAll(".session-item").forEach(el => {
      el.classList.toggle("active", el.getAttribute("onclick")?.includes(id));
    });
    if (window.innerWidth <= 680) document.getElementById("sidebar").classList.remove("open");
  } catch {}
}

// ── Memories ────────────────────────────────────────────────────
async function loadMemories() {
  try {
    const d    = await api("/api/memories");
    const list = document.getElementById("memories-list");
    if (!d.memories.length) {
      list.innerHTML = `<div style="font-size:13px;color:var(--text-muted);padding:4px 2px">Nothing saved yet</div>`;
      return;
    }
    list.innerHTML = d.memories.map(m => `
      <div class="memory-item">
        <span title="${esc(m.content)}">${esc(m.content.substring(0, 55))}${m.content.length > 55 ? "…" : ""}</span>
        <button class="btn-del-memory" onclick="deleteMemory(${m.id})" title="Remove">&#x2715;</button>
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
  const c = document.getElementById("memory-input").value.trim();
  if (!c) return;
  await fetch("/api/memories", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({content:c}) });
  closeAddMemory();
  loadMemories();
}
async function deleteMemory(id) {
  await fetch(`/api/memories/${id}`, { method:"DELETE" });
  loadMemories();
}

// ── Thinking indicator ──────────────────────────────────────────
function showThinking(show) {
  const el = document.getElementById("thinking");
  if (show) {
    el.classList.remove("hidden");
    const lbl = document.getElementById("thinking-label");
    if (selectedModel === "all") {
      lbl.textContent = "Searching across all models...";
    } else {
      const m = availableModels.find(x => x.id === selectedModel);
      lbl.textContent = `${m ? m.label : "AI"} is thinking...`;
    }
  } else {
    el.classList.add("hidden");
  }
}

// Badge helper kept as a no-op safety net (badges removed from thinking UI)
function setBadge(id, cls) {
  const el = document.getElementById(id);
  if (el) el.className = "thinking-badge" + (cls ? " " + cls : "");
}

// ── Sidebar ─────────────────────────────────────────────────────
function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("open");
}
document.addEventListener("click", (e) => {
  if (window.innerWidth > 680) return;
  const sb = document.getElementById("sidebar");
  if (sb.classList.contains("open") && !sb.contains(e.target) && !e.target.closest(".topbar-menu")) {
    sb.classList.remove("open");
  }
});

// ── Helpers ─────────────────────────────────────────────────────
function appendUserMsg(text) {
  const msgs = document.getElementById("messages");
  const div  = document.createElement("div");
  div.className = "msg user";
  div.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  msgs.appendChild(div);
  scrollBottom();
}

function esc(s) {
  return String(s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

function scrollBottom() {
  const msgs = document.getElementById("messages");
  requestAnimationFrame(() => { msgs.scrollTop = msgs.scrollHeight; });
}

function setSendDisabled(d) { document.getElementById("send-btn").disabled = d; }

function handleKey(e) {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

async function api(path) {
  const r = await fetch(path);
  return r.json();
}
