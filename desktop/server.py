"""Karaoke Studio – lokaler Server für Windows/Mac/Linux.

Startet die Web-App unter http://localhost:8765 (und im WLAN unter http://<PC-IP>:8765)
und verarbeitet Songs direkt auf diesem Rechner – mit NVIDIA-Grafikkarte, falls vorhanden.
Nur Python-Standardbibliothek + die Pipeline-Pakete, kein Web-Framework nötig.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import queue
import re
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP_DIR = ROOT / "app"
DATA = Path(os.environ.get("KARAOKE_DATA", ROOT / "Daten")).resolve()
PORT = int(os.environ.get("KARAOKE_PORT", "8765"))

sys.path.insert(0, str(ROOT / "pipeline"))

for d in ("songs", "videos", "inbox"):
    (DATA / d).mkdir(parents=True, exist_ok=True)

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("video/mp4", ".mp4")

SAFE_ID = re.compile(r"^[a-z0-9-]+$")


# ------------------------------------------------------------------ Geräte-Info

def gpu_info() -> dict:
    try:
        import torch
        if torch.cuda.is_available():
            return {"device": "cuda", "gpu": torch.cuda.get_device_name(0)}
    except Exception:
        pass
    return {"device": "cpu", "gpu": None}


def lan_urls() -> list[str]:
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
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return [f"http://{ip}:{PORT}" for ip in sorted(ips)]


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
        self.log = io.StringIO()

    def as_dict(self) -> dict:
        tail = self.log.getvalue()[-1500:]
        return {k: getattr(self, k) for k in ("id", "kind", "label", "state", "stage", "error",
                                              "created", "started", "finished", "result")} | {"log": tail}


jobs: list[Job] = []
job_queue: "queue.Queue[Job]" = queue.Queue()
jobs_lock = threading.Lock()


class Tee(io.TextIOBase):
    """Schreibt Ausgaben gleichzeitig ins Konsolenfenster und ins Job-Log."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def worker():
    import process as pipeline   # schwere Imports erst hier
    import render as renderer
    real_out = sys.__stdout__
    while True:
        job = job_queue.get()
        job.state, job.started = "running", time.time()
        old = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = Tee(real_out, job.log)
        try:
            if job.kind == "song":
                p = job.params
                meta = pipeline.process_song(
                    url=p.get("url", ""), file=p.get("file", ""), artist=p.get("artist", ""),
                    title=p.get("title", ""), added_by=p.get("added_by", ""), data_dir=DATA,
                    stage=lambda name: setattr(job, "stage", name))
                job.result = {"song_id": meta["id"], "title": meta["title"], "artist": meta["artist"]}
                if p.get("file"):
                    try:
                        (DATA / p["file"]).unlink()
                    except OSError:
                        pass
            else:
                sid, vol = job.params["song_id"], int(job.params["vocals"])
                job.stage = "Video"
                out = DATA / "videos" / sid / f"{sid}-gesang-{vol}.mp4"
                renderer.render(DATA / "songs" / sid, vol, out)
                job.result = {"song_id": sid, "video": out.name}
            job.state = "done"
        except Exception as e:
            traceback.print_exc()
            job.state = "failed"
            msg = str(e)
            if "Sign in to confirm" in job.log.getvalue() or "not a bot" in job.log.getvalue():
                msg = "YouTube hat den Download blockiert. Versuche es später noch einmal oder lade eine MP3 hoch."
            job.error = msg[:400]
        finally:
            sys.stdout, sys.stderr = old
            job.finished = time.time()
            job_queue.task_done()


def add_job(kind: str, label: str, params: dict) -> Job:
    job = Job(kind, label, params)
    with jobs_lock:
        jobs.append(job)
        del jobs[:-50]
    job_queue.put(job)
    return job


# ------------------------------------------------------------------ HTTP

def read_meta(song_dir: Path) -> dict | None:
    try:
        return json.loads((song_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "KaraokeStudio/1.0"

    def log_message(self, fmt, *args):  # Konsole ruhig halten
        if self.path.startswith("/api/") and not self.path.startswith("/api/jobs"):
            sys.__stdout__.write(f"[{time.strftime('%H:%M:%S')}] {self.command} {self.path}\n")

    # --- Hilfen
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

    def send_file(self, path: Path, cache: str = "no-cache"):
        if not path.is_file():
            return self.send_json({"error": "nicht gefunden"}, 404)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and size:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            elif m.group(2):
                start = max(0, size - int(m.group(2)))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", cache)
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

    def safe_path(self, base: Path, rel: str) -> Path | None:
        p = (base / urllib.parse.unquote(rel)).resolve()
        return p if p.is_relative_to(base.resolve()) else None

    # --- Routen
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path

        if path == "/api/health":
            return self.send_json({"ok": True, "local": True, **gpu_info(), "lan": lan_urls(),
                                   "data": str(DATA)})
        if path == "/api/songs":
            out = []
            for d in (DATA / "songs").iterdir():
                if d.is_dir():
                    meta = read_meta(d)
                    if meta:
                        out.append(meta)
            out.sort(key=lambda m: m.get("added", ""), reverse=True)
            return self.send_json(out)
        if path == "/api/jobs":
            with jobs_lock:
                return self.send_json([j.as_dict() for j in jobs])
        m = re.match(r"^/api/videos/([a-z0-9-]+)$", path)
        if m:
            vdir = DATA / "videos" / m.group(1)
            files = sorted(vdir.glob("*.mp4")) if vdir.is_dir() else []
            return self.send_json([{"name": f.name, "size": f.stat().st_size,
                                    "path": f"{m.group(1)}/{f.name}", "type": "file"} for f in files])
        if path.startswith("/files/"):
            p = self.safe_path(DATA, path[len("/files/"):])
            return self.send_file(p) if p else self.send_json({"error": "verboten"}, 403)

        # App-Dateien
        rel = path.lstrip("/") or "index.html"
        p = self.safe_path(APP_DIR, rel)
        if p and p.is_dir():
            p = p / "index.html"
        return self.send_file(p) if p else self.send_json({"error": "verboten"}, 403)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/songs":
            b = self.read_json()
            url = (b.get("url") or "").strip()
            if not re.match(r"^https?://(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", url):
                return self.send_json({"error": "Bitte einen YouTube-Link angeben."}, 400)
            job = add_job("song", b.get("title") or url, {
                "url": url, "artist": b.get("artist", ""), "title": b.get("title", ""),
                "added_by": b.get("added_by", "")})
            return self.send_json(job.as_dict(), 202)
        if path == "/api/export":
            b = self.read_json()
            sid = str(b.get("song_id", ""))
            try:
                vol = max(0, min(100, int(b.get("vocals", 0))))
            except (TypeError, ValueError):
                vol = 0
            if not SAFE_ID.match(sid) or not (DATA / "songs" / sid).is_dir():
                return self.send_json({"error": "Song nicht gefunden."}, 404)
            meta = read_meta(DATA / "songs" / sid) or {}
            job = add_job("export", f"{meta.get('title', sid)} – Gesang {vol} %", {"song_id": sid, "vocals": vol})
            return self.send_json(job.as_dict(), 202)
        m = re.match(r"^/api/songs/([a-z0-9-]+)/delete$", path)
        if m:
            sid = m.group(1)
            trash = DATA / "papierkorb"
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
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        name = re.sub(r"[^\w.\- ]+", "_", q.get("name", "song.mp3"))[-80:] or "song.mp3"
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 500 * 1024 * 1024:
            return self.send_json({"error": "Datei leer oder zu groß (max. 500 MB)."}, 400)
        rel = f"inbox/{uuid.uuid4().hex[:6]}-{name}"
        with open(DATA / rel, "wb") as f:
            left = n
            while left > 0:
                chunk = self.rfile.read(min(1024 * 1024, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        title = q.get("title") or Path(name).stem
        job = add_job("song", title, {"file": rel, "artist": q.get("artist", ""), "title": q.get("title", ""),
                                      "added_by": q.get("added_by", "")})
        return self.send_json(job.as_dict(), 202)


def main():
    info = gpu_info()
    print("=" * 60)
    print("  Karaoke Studio läuft!")
    print(f"  Am PC:    http://localhost:{PORT}")
    for u in lan_urls():
        print(f"  Im WLAN:  {u}   (Handy, Tablet, Fernseher)")
    print(f"  Rechnet mit: {'Grafikkarte – ' + info['gpu'] if info['gpu'] else 'Prozessor (keine NVIDIA-Grafikkarte gefunden)'}")
    print(f"  Songs liegen in: {DATA}")
    print("  Zum Beenden dieses Fenster schließen.")
    print("=" * 60)
    threading.Thread(target=worker, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    if os.environ.get("KARAOKE_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
