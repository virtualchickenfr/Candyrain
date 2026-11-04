"""Karaoke Studio – Windows-Programm (wird mit PyInstaller zu „Karaoke Studio.exe“).

Ein Fenster, kein Browser, keine Konsole. Die Oberfläche läuft in Microsofts WebView2
(ist in Windows 10/11 eingebaut).
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import DATA, FROZEN, IS_WIN, LOCAL_APP, LOG_FILE  # noqa: E402

APP_NAME = "Karaoke Studio"


def _setup_logging(path: Path):
    """Im Fenster-Modus gibt es kein stdout – alles in eine Logdatei schreiben."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f
    print(f"\n===== {APP_NAME} gestartet {time.strftime('%Y-%m-%d %H:%M:%S')} =====")


def _message(text: str, title: str = APP_NAME):
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    else:
        print(text)


def _single_instance() -> bool:
    if not IS_WIN:
        return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "KaraokeStudio_SingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183   # ERROR_ALREADY_EXISTS


class Api:
    """Funktionen, die die Oberfläche über window.pywebview.api aufrufen kann."""

    def __init__(self):
        self.window = None

    def toggle_fullscreen(self):
        if self.window:
            self.window.toggle_fullscreen()
        return True


def run_app():
    if FROZEN or IS_WIN:
        _setup_logging(LOG_FILE)
    if not _single_instance():
        _message("Karaoke Studio läuft schon. Schau in der Taskleiste nach.")
        return
    import server
    port = server.start()

    try:
        import webview
    except Exception as e:
        _message(f"Das Fenster konnte nicht geöffnet werden:\n{e}")
        return

    api = Api()
    window = webview.create_window(APP_NAME, f"http://127.0.0.1:{port}/", width=1200, height=820,
                                   min_size=(380, 600), background_color="#24112C", js_api=api,
                                   text_select=False)
    api.window = window
    try:
        webview.start(gui="edgechromium" if IS_WIN else None, private_mode=False,
                      storage_path=str(LOCAL_APP / "webview"))
    except Exception as e:
        print("Fenster-Fehler:", e)
        _message("Karaoke Studio braucht „Microsoft Edge WebView2“.\n\n"
                 "Bitte installiere es (kostenlos) von:\n"
                 "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
                 "und starte Karaoke Studio danach erneut.")
    finally:
        server.stop_all()
        os._exit(0)


# ------------------------------------------------------------------ Selbsttest (für GitHub Actions)

def selftest(log_path: str) -> int:
    _setup_logging(Path(log_path))
    import subprocess
    import json
    import server
    import setup_tools as tools

    ok = True
    print("SELBSTTEST – Daten:", DATA)

    # 1) Fenster-Bibliothek lässt sich laden
    try:
        import webview  # noqa: F401
        print("OK: pywebview importiert")
    except Exception as e:
        print("FEHLER: pywebview:", e)
        ok = False

    # 2) Einrichtung
    t0 = time.time()
    if not tools.is_ready():
        if not tools.run_blocking():
            print("FEHLER: Einrichtung:", tools.status.error)
            print(tools.status.as_dict()["log"])
            return 1
    print(f"OK: Einrichtung in {time.time() - t0:.0f}s – {tools.info()}")

    # 3) Test-Audio erzeugen und als Song verarbeiten
    test = DATA / "inbox" / "selbsttest.mp3"
    subprocess.run([str(tools.FF_DIR / "ffmpeg.exe" if IS_WIN else "ffmpeg"), "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=f=220:d=25", "-f", "lavfi", "-i", "sine=f=660:d=25",
                    "-filter_complex", "amix=inputs=2", "-ac", "2", str(test)], check=True,
                   creationflags=tools.NO_WINDOW)
    job = server.Job("song", "Selbsttest", {"file": str(test), "artist": "Karaoke Studio", "title": "Selbsttest"})
    t0 = time.time()
    server.execute(job)
    print(f"Song-Auftrag: {job.state} in {time.time() - t0:.0f}s, Stufe {job.stage}, Ergebnis {job.result}")
    if job.state != "done":
        print("FEHLER:", job.error)
        print("\n".join(job.lines[-60:]))
        return 1
    sid = job.result["song_id"]
    sdir = DATA / "songs" / sid
    for f in ("meta.json", "lyrics.json", "instrumental.mp3", "vocals.mp3"):
        if not (sdir / f).exists():
            print("FEHLER: fehlt", f)
            ok = False
    vids = list((DATA / "videos" / sid).glob("*.mp4"))
    print("Videos:", [v.name for v in vids])
    ok &= bool(vids)

    # 4) Export mit 30 % Gesang
    exp = server.Job("export", "Export", {"song_id": sid, "vocals": 30})
    server.execute(exp)
    print("Export:", exp.state, exp.result, exp.error)
    ok &= exp.state == "done"

    # 5) Server-Antwort
    port = server.start()
    import urllib.request
    h = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health").read())
    songs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/songs").read())
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read()
    print("Health:", {k: h[k] for k in ("ready", "device", "gpu")}, "Songs:", len(songs), "Seite:", len(page), "Bytes")
    ok &= h["ready"] and len(songs) >= 1 and b"Karaoke" in page

    # 6) Fenster wirklich öffnen (nicht fatal, falls der Testrechner keine Oberfläche hat)
    try:
        import webview
        result = {}
        win = webview.create_window(APP_NAME, f"http://127.0.0.1:{port}/", width=900, height=700)

        def check():
            time.sleep(6)
            try:
                result["brand"] = win.evaluate_js("document.querySelector('.brand-small').textContent")
                result["songs"] = win.evaluate_js("document.querySelectorAll('.song').length")
            except Exception as e:
                result["error"] = str(e)
            win.destroy()

        threading.Thread(target=check, daemon=True).start()
        threading.Timer(60, lambda: win.destroy()).start()
        webview.start(gui="edgechromium" if IS_WIN else None, private_mode=True)
        print("Fenster-Test:", result)
    except Exception as e:
        print("Fenster-Test nicht möglich:", e)

    print("SELBSTTEST", "BESTANDEN" if ok else "FEHLGESCHLAGEN")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        code = selftest(sys.argv[i + 1] if len(sys.argv) > i + 1 else "selftest.log")
        sys.stdout.flush()
        os._exit(code)
    run_app()
