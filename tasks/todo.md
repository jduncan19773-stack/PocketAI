# PocketAI — Current Todo

Created: 2026-06-04
Branch: feature/phase1-complete

---

## Goal
Get PocketAI fully runnable, polished, and voice-capable so a non-technical person
can double-click one file and be chatting (and speaking) with their private AI.

---

## Todo

### Phase 1 — Make it launchable
- [ ] Create `setup.bat` — one-time first-run: creates venv, installs requirements
- [ ] Create `run.py` — Python launcher: checks Ollama is running, starts uvicorn on port 7860, opens browser
- [ ] Create `start.bat` — double-click launcher that calls run.py via the venv Python
- [ ] Init git repo + create feature branch + first commit of all existing code

### Phase 2 — Fix voice (output toggle is wired to nothing)
- [ ] Add a 🔊 "Speak responses" toggle button to the UI input bar
- [ ] Wire it to `toggleVoiceResponse()` in app.js (already exists, just disconnected)
- [ ] Make voice button (🎤) larger and more obvious — add label text
- [ ] Improve voice status messages to plain English ("Listening... speak now" is fine, but "Error: not-allowed" needs to say "Microphone blocked — please allow mic access")

### Phase 3 — UI polish for non-technical users
- [ ] Bump base font size from 17px → 19px (easier for older eyes)
- [ ] Make the empty state more welcoming — bigger icon, friendlier greeting copy
- [ ] Add tooltip/hint text to the voice button on first load ("Click here to speak")
- [ ] Replace "My Notes" sidebar label with "Things AI Remembers About Me"
- [ ] Add a loading spinner/message during startup before Ollama is ready

### Phase 4 — Update tracking
- [ ] Update tasks/progress.txt with what has been built

---

## Review
*(filled in after all tasks complete)*
