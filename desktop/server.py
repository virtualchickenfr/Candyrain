"""Karaoke Studio – interner Server der Desktop-App.

Liefert die Oberfläche an das App-Fenster und erledigt die Arbeit im Hintergrund.
Die KI-Verarbeitung läuft als unsichtbarer Unterprozess mit der eingerichteten Python-Kopie
(dadurch bleibt die .exe klein). Optional auch im WLAN für Handy/Tablet erreichbar.
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup_tools as tools  # noqa: E402
from paths import APP_DIR, DATA, IS_WIN, NO_WINDOW, PIPELINE_DIR, SETTINGS_FILE  # noqa: E402

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

SAFE_ID = re.compile(r"^[a-z0-9-]+$")
LAN_PORTS = range(8765, 8775)


def log(*args):
    try:
        print(f"[{time.strftime('%H:%M:%S')}]", *args, flush=True)
    except Exception:
        pass


# ------------------------------------------------------------------ Einstellungen

def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(**kw):
    s = load_settings() | kw
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ Aufträge

class Job:
    def __init__(self, kind: str, label: str, params: dict):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind            # "song" | "export"
        self.label = label
        self.params = params
        self.state = "queued"       # queued | running | done | failed
        self.stage = ""
        self.error = ""
        self.created = time.time()
        self.started = None
        self.finished = None
        self.result = None
        self.lines: list[str] = []
        self.proc: subprocess.Popen | None = None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "kind", "label", "state", "stage", "error",
                                              "created", "started", "finished", "result")} | \
               {"log": "\n".join(self.lines[-25:])}


jobs: list[Job] = []
job_queue: "queue.Queue[Job]" = queue.Queue()
jobs_lock = threading.Lock()


def _friendly(lines: list[str]) -> str:
    text = "\n".join(lines)
    if "Sign in to confirm" in text or "not a bot" in text:
        return "YouTube hat den Download blockiert. Versuche es später noch einmal oder lade eine MP3 hoch."
    if "Video unavailable" in text or "Private video" in text:
        return "Dieses YouTube-Video ist nicht verfügbar."
    if "CUDA out of memory" in text or "OutOfMemoryError" in text:
        return "Der Grafikkarte ist der Speicher ausgegangen. Schließe Spiele/andere Programme und versuche es erneut."
    for line in reversed(lines):
        line = line.strip()
        if line.startswith(("ERROR", "RuntimeError", "FileNotFoundError", "ValueError")) or "Error:" in line:
            return line[:300]
    return "Unbekannter Fehler – Details stehen im Protokoll."


def _run_pipeline(job: Job, args: list[str]):
    env = tools.tool_env()
    cmd = [tools.python_exe(), "-u", *args]
    log("Starte:", " ".join(cmd[:4]), "…")
    job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", env=env, cwd=str(DATA),
                                creationflags=NO_WINDOW)
    for raw in job.proc.stdout:
        for line in raw.replace("\r", "\n").splitlines():
            if line.startswith("@@STAGE "):
                job.stage = line[8:].strip()
            elif line.startswith("@@RESULT "):
                try:
                    job.result = json.loads(line[9:])
                except json.JSONDecodeError:
                    pass
            elif line.strip():
                job.lines.append(line[:400])
                del job.lines[:-400]
                log(line[:200])
    if job.proc.wait() != 0:
        raise RuntimeError(_friendly(job.lines))


def execute(job: Job):
    """Führt einen Auftrag aus (auch direkt vom Selbsttest aufgerufen)."""
    job.state, job.started = "running", time.time()
    try:
        if not tools.is_ready():
            raise RuntimeError("Karaoke Studio ist noch nicht eingerichtet.")
        if job.kind == "song":
            p = job.params
            args = [str(PIPELINE_DIR / "process.py"), "--data-dir", str(DATA),
                    "--artist", p.get("artist", ""), "--title", p.get("title", ""),
                    "--added-by", p.get("added_by", "")]
            args += ["--url", p["url"]] if p.get("url") else ["--file", p["file"]]
            _run_pipeline(job, args)
            if p.get("file"):
                try:
                    Path(p["file"]).unlink()
                except OSError:
                    pass
        elif job.kind in ("resync", "notes"):
            sid = job.params["song_id"]
            flags = ["--notes"] if job.kind == "notes" else ["--lyrics", "--notes"]
            _run_pipeline(job, [str(PIPELINE_DIR / "resync.py"), str(DATA / "songs" / sid), *flags])
            job.result = {"song_id": sid}
        else:
            sid, vol = job.params["song_id"], int(job.params["vocals"])
            off = float(job.params.get("offset") or 0)
            job.stage = "Video"
            out = DATA / "videos" / sid / f"{sid}-gesang-{vol}.mp4"
            _run_pipeline(job, [str(PIPELINE_DIR / "render.py"), str(DATA / "songs" / sid),
                                "--vocals", str(vol), "--offset", f"{off:.2f}", "--out", str(out)])
            if not out.exists():
                raise RuntimeError("Das Video wurde nicht erstellt.")
            job.result = {"song_id": sid, "video": out.name}
        job.state = "done"
    except Exception as e:
        log(traceback.format_exc())
        job.state = "failed"
        job.error = str(e)[:400]
    finally:
        job.finished = time.time()
        job.proc = None


def worker():
    while True:
        job = job_queue.get()
        execute(job)
        job_queue.task_done()


def add_job(kind: str, label: str, params: dict) -> Job:
    job = Job(kind, label, params)
    with jobs_lock:
        jobs.append(job)
        del jobs[:-50]
    job_queue.put(job)
    return job


def queue_missing_notes():
    """Für ältere Songs ohne SingStar-Noten diese im Hintergrund nachberechnen."""
    if not tools.is_ready():
        return 0
    n = 0
    for d in sorted((DATA / "songs").iterdir()):
        if d.is_dir() and (d / "vocals.mp3").exists() and not (d / "notes.json").exists():
            meta = read_meta(d) or {}
            add_job("notes", meta.get("title", d.name), {"song_id": d.name})
            n += 1
    if n:
        log(f"Berechne Noten für {n} ältere Songs im Hintergrund")
    return n


def stop_all():
    for j in list(jobs):
        if j.proc and j.proc.poll() is None:
            try:
                if IS_WIN:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(j.proc.pid)],
                                   capture_output=True, creationflags=NO_WINDOW)
                else:
                    j.proc.kill()
            except Exception:
                pass


# ------------------------------------------------------------------ WLAN

lan_server: ThreadingHTTPServer | None = None


def lan_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if not info[4][0].startswith("127."):
                ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def lan_urls() -> list[str]:
    if not lan_server:
        return []
    port = lan_server.server_address[1]
    return [f"http://{ip}:{port}" for ip in lan_ips()]


def set_lan(enabled: bool) -> list[str]:
    global lan_server
    if enabled and not lan_server:
        for port in LAN_PORTS:
            try:
                lan_server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
                break
            except OSError:
                continue
        if lan_server:
            threading.Thread(target=lan_server.serve_forever, daemon=True).start()
            log("WLAN-Zugriff an:", lan_urls())
    elif not enabled and lan_server:
        srv, lan_server = lan_server, None
        threading.Thread(target=srv.shutdown, daemon=True).start()
        log("WLAN-Zugriff aus")
    save_settings(lan=bool(enabled))
    return lan_urls()


# ------------------------------------------------------------------ HTTP

def read_meta(song_dir: Path) -> dict | None:
    try:
        return json.loads((song_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def health() -> dict:
    d = tools.info() or {}
    return {"ok": True, "local": True, "ready": tools.is_ready(), "device": d.get("device", "cpu"),
            "gpu": d.get("gpu"), "lan": lan_urls(), "lan_enabled": bool(lan_server), "data": str(DATA),
            "setup": tools.status.as_dict()}


class Handler(BaseHTTPRequestHandler):
    server_version = "KaraokeStudio/2.0"

    def log_message(self, fmt, *args):
        pass

    @property
    def is_local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def send_file(self, path: Path | None):
        if not path or not path.is_file():
            return self.send_json({"error": "nicht gefunden"}, 404)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and size and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:
                start = max(0, size - int(m.group(2)))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass

    @staticmethod
    def safe_path(base: Path, rel: str) -> Path | None:
        p = (base / urllib.parse.unquote(rel)).resolve()
        return p if p.is_relative_to(base.resolve()) else None

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            return self.send_json(health())
        if path == "/api/setup":
            return self.send_json(tools.status.as_dict())
        if path == "/api/songs":
            out = [m for d in (DATA / "songs").iterdir() if d.is_dir() and (m := read_meta(d))]
            out.sort(key=lambda m: m.get("added", ""), reverse=True)
            return self.send_json(out)
        if path == "/api/jobs":
            with jobs_lock:
                return self.send_json([j.as_dict() for j in jobs])
        m = re.match(r"^/api/videos/([a-z0-9-]+)$", path)
        if m:
            vdir = DATA / "videos" / m.group(1)
            files = sorted(vdir.glob("*.mp4")) if vdir.is_dir() else []
            return self.send_json([{"name": f.name, "size": f.stat().st_size, "type": "file",
                                    "path": f"{m.group(1)}/{f.name}"} for f in files])
        if path.startswith("/files/"):
            return self.send_file(self.safe_path(DATA, path[len("/files/"):]))
        rel = path.lstrip("/") or "index.html"
        p = self.safe_path(APP_DIR, rel)
        if p and p.is_dir():
            p = p / "index.html"
        return self.send_file(p)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        b = self.read_json()

        # Nur am PC selbst erlaubt
        if path in ("/api/setup/start", "/api/lan", "/api/reveal", "/api/open-data"):
            if not self.is_local:
                return self.send_json({"error": "Nur am PC möglich."}, 403)
            if path == "/api/setup/start":
                return self.send_json(tools.start())
            if path == "/api/lan":
                return self.send_json({"lan": set_lan(bool(b.get("enabled")))})
            if path == "/api/open-data":
                _reveal(DATA, select=False)
                return self.send_json({"ok": True})
            p = self.safe_path(DATA, str(b.get("path", "")))
            if not p or not p.exists():
                return self.send_json({"error": "Datei nicht gefunden."}, 404)
            _reveal(p, select=True)
            return self.send_json({"ok": True})

        if path == "/api/songs":
            if not tools.is_ready():
                return self.send_json({"error": "Bitte Karaoke Studio zuerst am PC einrichten."}, 409)
            url = (b.get("url") or "").strip()
            if not re.match(r"^https?://(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", url):
                return self.send_json({"error": "Bitte einen YouTube-Link angeben."}, 400)
            job = add_job("song", b.get("title") or url, {
                "url": url, "artist": b.get("artist", ""), "title": b.get("title", ""),
                "added_by": b.get("added_by", "")})
            return self.send_json(job.as_dict(), 202)
        if path == "/api/export":
            sid = str(b.get("song_id", ""))
            try:
                vol = max(0, min(100, int(b.get("vocals", 0))))
            except (TypeError, ValueError):
                vol = 0
            if not SAFE_ID.match(sid) or not (DATA / "songs" / sid).is_dir():
                return self.send_json({"error": "Song nicht gefunden."}, 404)
            try:
                off = max(-5.0, min(5.0, float(b.get("offset") or 0)))
            except (TypeError, ValueError):
                off = 0.0
            meta = read_meta(DATA / "songs" / sid) or {}
            job = add_job("export", f"{meta.get('title', sid)} – Gesang {vol} %",
                          {"song_id": sid, "vocals": vol, "offset": off})
            return self.send_json(job.as_dict(), 202)
        if path == "/api/resync":
            sid = str(b.get("song_id", ""))
            if not tools.is_ready():
                return self.send_json({"error": "Bitte Karaoke Studio zuerst am PC einrichten."}, 409)
            if not SAFE_ID.match(sid) or not (DATA / "songs" / sid).is_dir():
                return self.send_json({"error": "Song nicht gefunden."}, 404)
            meta = read_meta(DATA / "songs" / sid) or {}
            job = add_job("resync", meta.get("title", sid), {"song_id": sid})
            return self.send_json(job.as_dict(), 202)
        m = re.match(r"^/api/songs/([a-z0-9-]+)/delete$", path)
        if m:
            sid = m.group(1)
            trash = DATA / "Papierkorb"
            trash.mkdir(exist_ok=True)
            for sub in ("songs", "videos"):
                src = DATA / sub / sid
                if src.exists():
                    shutil.move(str(src), str(trash / f"{sub}-{sid}"))
            return self.send_json({"ok": True})
        self.send_json({"error": "unbekannt"}, 404)

    def do_PUT(self):
        url = urllib.parse.urlparse(self.path)
        if url.path != "/api/upload":
            return self.send_json({"error": "unbekannt"}, 404)
        if not tools.is_ready():
            return self.send_json({"error": "Bitte Karaoke Studio zuerst am PC einrichten."}, 409)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        name = re.sub(r"[^\w.\- ]+", "_", q.get("name", "song.mp3"))[-80:] or "song.mp3"
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 500 * 1024 * 1024:
            return self.send_json({"error": "Datei leer oder zu groß (max. 500 MB)."}, 400)
        dest = DATA / "inbox" / f"{uuid.uuid4().hex[:6]}-{name}"
        with open(dest, "wb") as f:
            left = n
            while left > 0:
                chunk = self.rfile.read(min(1024 * 1024, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        job = add_job("song", q.get("title") or Path(name).stem, {
            "file": str(dest), "artist": q.get("artist", ""), "title": q.get("title", ""),
            "added_by": q.get("added_by", "")})
        return self.send_json(job.as_dict(), 202)


def _reveal(p: Path, select: bool):
    try:
        if IS_WIN:
            if select:
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(p.parent if select else p)])
    except Exception as e:
        log("Ordner öffnen fehlgeschlagen:", e)


# ------------------------------------------------------------------ Start

internal_server: ThreadingHTTPServer | None = None


def start(port: int = 0) -> int:
    """Startet Server + Arbeits-Thread. Gibt den internen Port zurück (nur 127.0.0.1)."""
    global internal_server
    internal_server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=internal_server.serve_forever, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    if load_settings().get("lan"):
        set_lan(True)
    queue_missing_notes()
    port = internal_server.server_address[1]
    log(f"Interner Server auf Port {port}, Daten in {DATA}")
    return port


if __name__ == "__main__":   # Entwicklung: python desktop/server.py
    p = start(int(os.environ.get("KARAOKE_PORT", "8765")))
    print(f"http://127.0.0.1:{p}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop_all()
