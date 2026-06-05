"""
pocketai_app.py — Native window launcher for PocketAI.

Creates a standalone native window (no separate browser needed).
Starts the FastAPI server internally, then opens a webview window.
When the window is closed, everything shuts down cleanly.

Usage:  python pocketai_app.py           (development)
        PocketAI.exe                     (compiled with PyInstaller)

PyInstaller build:
  pyinstaller pocketai.spec
  (or: python build_exe.py)
"""

import os
import sys
import threading
import time
from pathlib import Path

# ── Path resolution ─────────────────────────────────────────────
# When frozen by PyInstaller, sys.executable is PocketAI.exe.
# We need HERE to be the *drive root* (where models/, bin/ etc. live),
# not sys._MEIPASS (the extracted temp dir).

if getattr(sys, "frozen", False):
    HERE = Path(sys.executable).parent
else:
    HERE = Path(__file__).resolve().parent

# ── Environment ─────────────────────────────────────────────────
os.chdir(str(HERE))
os.environ["POCKETAI_DATA"]  = str(HERE / "data")
os.environ["POCKETAI_MODELS"]= str(HERE / "models")
os.environ["PYTHONUTF8"]     = "1"

# USB mode: llama-server.exe is on the drive
if (HERE / "bin" / "llama-server.exe").exists():
    os.environ["POCKETAI_MODE"] = "usb"

# Make sure server/ is importable (needed inside PyInstaller bundle)
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

PORT = int(os.environ.get("POCKETAI_PORT", "7860"))
URL  = f"http://127.0.0.1:{PORT}"

# ── Server startup ───────────────────────────────────────────────

_server_ready = threading.Event()


def _run_server():
    """Start the FastAPI/uvicorn server in a background thread."""
    import uvicorn
    from server.main import app

    class _WaitFilter(uvicorn.config.Config):
        pass

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    # Signal ready after startup completes
    original_startup = server.startup

    async def startup_and_signal(sockets=None):
        await original_startup(sockets)
        _server_ready.set()

    server.startup = startup_and_signal

    import asyncio
    asyncio.run(server.serve())


def _start_server_thread():
    t = threading.Thread(target=_run_server, daemon=True)
    t.start()


def _wait_for_server(timeout: int = 180) -> bool:
    """Poll /api/status until the server is accepting requests."""
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{URL}/api/status", timeout=1.5)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.8)
    return False


# ── Webview window ───────────────────────────────────────────────

def _create_window():
    """Create and start the native webview window."""
    import webview

    window = webview.create_window(
        title        = "PocketAI",
        url          = URL,
        width        = 1280,
        height       = 840,
        min_size     = (900, 600),
        resizable    = True,
        background_color = "#080810",
    )

    def on_closed():
        # Clean shutdown when user closes the window
        os._exit(0)

    window.events.closed += on_closed
    webview.start(debug=False)


# ── Splash / loading ─────────────────────────────────────────────

_SPLASH_HTML = """
<!DOCTYPE html><html>
<head><meta charset="UTF-8">
<style>
  body { margin:0; background:#080810; display:flex; flex-direction:column;
         align-items:center; justify-content:center; height:100vh;
         font-family:system-ui,sans-serif; color:#e2e8f0; gap:20px; }
  .logo { width:72px; height:72px; background:linear-gradient(135deg,#6366f1,#06b6d4);
          border-radius:20px; display:flex; align-items:center; justify-content:center;
          font-size:36px; font-weight:800; color:#fff; }
  .title  { font-size:24px; font-weight:700; }
  .status { font-size:15px; color:#64748b; }
  .bar    { width:220px; height:3px; background:#1e2030; border-radius:4px; overflow:hidden; }
  .fill   { height:100%; background:linear-gradient(90deg,#6366f1,#06b6d4);
            animation:slide 2s ease-in-out infinite; }
  @keyframes slide { 0%{width:0;margin-left:0} 50%{width:60%;margin-left:0} 100%{width:0;margin-left:100%} }
</style></head>
<body>
  <div class="logo">P</div>
  <div class="title">PocketAI</div>
  <div class="status" id="s">Loading AI model...</div>
  <div class="bar"><div class="fill"></div></div>
  <div class="status" style="font-size:13px">This takes about 60 seconds on first launch</div>
</body></html>
"""


def main():
    import webview

    # Show splash while loading
    splash = webview.create_window(
        title            = "PocketAI — Loading...",
        html             = _SPLASH_HTML,
        width            = 520,
        height           = 360,
        resizable        = False,
        background_color = "#080810",
    )

    def on_splash_shown():
        # Start server
        _start_server_thread()

        # Wait for server (with live countdown)
        ready = _wait_for_server(timeout=180)

        if ready:
            # Open the real window, close the splash
            win = webview.create_window(
                title            = "PocketAI",
                url              = URL,
                width            = 1280,
                height           = 840,
                min_size         = (900, 600),
                resizable        = True,
                background_color = "#080810",
            )
            win.events.closed += lambda: os._exit(0)
            splash.destroy()
        else:
            splash.load_html("""
              <html><body style="background:#080810;color:#ef4444;font-family:system-ui;
                display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;gap:16px;flex-direction:column;">
                <div style="font-size:40px">&#9888;</div>
                <div style="font-size:18px;font-weight:600">Could not start AI model</div>
                <div style="font-size:14px;color:#94a3b8">Run SETUP.bat to download required files.</div>
                <button onclick="window.pywebview.api.quit()" style="margin-top:10px;padding:10px 24px;background:#6366f1;border:none;border-radius:8px;color:#fff;font-size:14px;cursor:pointer">Close</button>
              </body></html>
            """)

    webview.start(on_splash_shown, debug=False)


if __name__ == "__main__":
    main()
