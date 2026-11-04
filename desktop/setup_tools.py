"""Einmalige Einrichtung der KI-Werkzeuge – läuft unsichtbar im Hintergrund der App.

Lädt eine eigene Python-Kopie, PyTorch (mit NVIDIA-Grafikkarte: CUDA), Demucs, Whisper,
yt-dlp, ffmpeg und Deno nach %LOCALAPPDATA%\\KaraokeStudio\\tools. Am System wird nichts verändert.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from paths import IS_WIN, NO_WINDOW, PIPELINE_DIR, TOOLS

VERSION = 1
PY_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"
FFMPEG_URLS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
]
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
TORCH = ["torch==2.5.1", "torchaudio==2.5.1"]

PY_DIR = TOOLS / "python"
FF_DIR = TOOLS / "ffmpeg"
DENO_DIR = TOOLS / "deno"
MODELS = TOOLS / "models"
INFO_FILE = TOOLS / "installed.json"

# (Schlüssel, Text für die Anzeige, Gewicht für den Fortschrittsbalken)
STEPS = [
    ("python", "Python einrichten", 5),
    ("gpu", "Grafikkarte prüfen", 1),
    ("torch", "KI-Grundlage laden (PyTorch)", 34),
    ("packages", "Gesangstrennung, Lyrics und YouTube-Download installieren", 12),
    ("ffmpeg", "Audio- und Video-Werkzeug laden (ffmpeg)", 10),
    ("deno", "Hilfsprogramm für YouTube laden", 3),
    ("models", "KI-Modelle laden", 33),
    ("check", "Abschluss-Prüfung", 2),
]


# ------------------------------------------------------------------ Öffentliche Hilfen

def python_exe() -> str:
    if os.environ.get("KARAOKE_PYTHON"):
        return os.environ["KARAOKE_PYTHON"]
    return str(PY_DIR / ("python.exe" if IS_WIN else "bin/python3"))


def info() -> dict | None:
    if os.environ.get("KARAOKE_PYTHON"):          # Entwicklungsmodus
        return {"version": VERSION, "device": os.environ.get("KARAOKE_DEVICE", "cpu"), "gpu": None}
    try:
        data = json.loads(INFO_FILE.read_text(encoding="utf-8"))
        return data if data.get("version") == VERSION else None
    except Exception:
        return None


def is_ready() -> bool:
    return info() is not None


def tool_env() -> dict:
    env = os.environ.copy()
    extra = [str(FF_DIR), str(DENO_DIR), str(PY_DIR), str(PY_DIR / "Scripts")]
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["TORCH_HOME"] = str(MODELS / "torch")
    env["XDG_CACHE_HOME"] = str(MODELS)            # Whisper-Modelle
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    d = info()
    if d and d.get("device") == "cpu":
        env["KARAOKE_DEVICE"] = "cpu"
    return env


# ------------------------------------------------------------------ Status

class Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.state = "idle"        # idle | running | done | failed
        self.step = ""
        self.step_label = ""
        self.done_weight = 0
        self.step_fraction = 0.0
        self.detail = ""
        self.error = ""
        self.log: list[str] = []

    def as_dict(self) -> dict:
        total = sum(w for _, _, w in STEPS)
        cur_w = next((w for k, _, w in STEPS if k == self.step), 0)
        progress = (self.done_weight + cur_w * self.step_fraction) / total
        if self.state == "done":
            progress = 1.0
        return {"state": self.state, "step": self.step, "label": self.step_label,
                "progress": round(min(progress, 1.0), 4), "detail": self.detail,
                "error": self.error, "log": "\n".join(self.log[-12:])}

    def add_log(self, line: str):
        line = line.rstrip()
        if line:
            self.log.append(line[:300])
            del self.log[:-200]
            print("[Einrichtung]", line, flush=True)


status = Status()
_thread: threading.Thread | None = None


# ------------------------------------------------------------------ Bausteine

def _download(url: str, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "KaraokeStudio/1.0"})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = r.read(512 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        status.step_fraction = min(0.95, got / total)
                        status.detail = f"{got / 1048576:.0f} von {total / 1048576:.0f} MB"
                    else:
                        status.detail = f"{got / 1048576:.0f} MB"
            return
        except Exception as e:
            last_err = e
            status.add_log(f"Download fehlgeschlagen (Versuch {attempt + 1}): {e}")
            time.sleep(3)
    raise RuntimeError(f"Download nicht möglich: {url} ({last_err})")


def _run(cmd: list[str], what: str):
    """Programm unsichtbar ausführen, Ausgabe ins Log, Fortschritt grob schätzen."""
    status.add_log("$ " + " ".join(str(c) for c in cmd[:6]))
    proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", env=tool_env(),
                            creationflags=NO_WINDOW, cwd=str(TOOLS))
    n = 0
    for line in proc.stdout:
        n += 1
        line = line.strip()
        if not line:
            continue
        status.add_log(line)
        if line.startswith(("Downloading", "Collecting", "Installing", "Successfully")):
            status.detail = line[:120]
        status.step_fraction = min(0.9, status.step_fraction + 0.01)
    if proc.wait() != 0:
        raise RuntimeError(f"{what} ist fehlgeschlagen. Details stehen im Protokoll.")


def _pip(*args: str, what: str):
    _run([python_exe(), "-m", "pip", "install", "--no-warn-script-location", "--progress-bar", "off",
          *args], what)


def detect_nvidia() -> str | None:
    if os.environ.get("KARAOKE_FORCE_CPU"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=20,
                             creationflags=NO_WINDOW)
        if out.returncode == 0 and "GPU" in out.stdout:
            first = out.stdout.strip().splitlines()[0]
            return first.split(":", 1)[1].split("(UUID")[0].strip()
    except Exception:
        pass
    if IS_WIN:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA' } "
                                  "| Select-Object -First 1).Name"],
                                 capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
            name = out.stdout.strip()
            if name:
                return name
        except Exception:
            pass
    return None


# ------------------------------------------------------------------ Ablauf

def _step(key: str):
    for k, label, w in STEPS:
        if k == status.step:
            status.done_weight += w
    status.step = key
    status.step_label = next(l for k, l, _ in STEPS if k == key)
    status.step_fraction = 0.0
    status.detail = ""
    status.add_log(f"== {status.step_label}")


def _install():
    if not IS_WIN:
        raise RuntimeError("Die automatische Einrichtung gibt es nur unter Windows.")
    TOOLS.mkdir(parents=True, exist_ok=True)
    tmp = TOOLS / "_download"
    tmp.mkdir(exist_ok=True)

    _step("python")
    if not (PY_DIR / "python.exe").exists():
        _download(PY_URL, tmp / "python.zip")
        with zipfile.ZipFile(tmp / "python.zip") as z:
            z.extractall(PY_DIR)
        for pth in PY_DIR.glob("python*._pth"):
            text = pth.read_text(encoding="ascii").replace("#import site", "import site")
            pth.write_text(text, encoding="ascii")
    if not (PY_DIR / "Scripts" / "pip.exe").exists():
        _download(GETPIP_URL, tmp / "get-pip.py")
        _run([python_exe(), str(tmp / "get-pip.py"), "--no-warn-script-location"], "pip-Installation")
    _pip("setuptools<81", "wheel", what="Grundpakete")

    _step("gpu")
    gpu = detect_nvidia()
    status.detail = f"Gefunden: {gpu}" if gpu else "Keine NVIDIA-Grafikkarte – nutze den Prozessor"
    status.add_log(status.detail)

    _step("torch")
    index = "https://download.pytorch.org/whl/cu121" if gpu else "https://download.pytorch.org/whl/cpu"
    status.detail = "Mit Grafikkarten-Unterstützung (ca. 2,5 GB)" if gpu else "Für den Prozessor (ca. 200 MB)"
    _pip(*TORCH, "--index-url", index, what="PyTorch-Installation")

    _step("packages")
    _pip("--no-build-isolation", "-r", str(PIPELINE_DIR / "requirements.txt"), what="Paket-Installation")

    _step("ffmpeg")
    if not (FF_DIR / "ffmpeg.exe").exists():
        last = None
        for url in FFMPEG_URLS:
            try:
                _download(url, tmp / "ffmpeg.zip")
                break
            except Exception as e:
                last = e
        else:
            raise RuntimeError(f"ffmpeg konnte nicht geladen werden: {last}")
        FF_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp / "ffmpeg.zip") as z:
            for name in z.namelist():
                base = name.rsplit("/", 1)[-1]
                if base in ("ffmpeg.exe", "ffprobe.exe"):
                    with z.open(name) as src, open(FF_DIR / base, "wb") as dst:
                        shutil.copyfileobj(src, dst)

    _step("deno")
    if not (DENO_DIR / "deno.exe").exists():
        _download(DENO_URL, tmp / "deno.zip")
        with zipfile.ZipFile(tmp / "deno.zip") as z:
            z.extractall(DENO_DIR)

    _step("models")
    device = "cpu"
    if gpu:
        out = subprocess.run([python_exe(), "-c", "import torch;print(torch.cuda.is_available())"],
                             capture_output=True, text=True, env=tool_env(), creationflags=NO_WINDOW)
        device = "cuda" if "True" in out.stdout else "cpu"
        if device == "cpu":
            status.add_log("Grafikkarte wird von PyTorch nicht unterstützt – nutze den Prozessor.")
    env_dev = "cuda" if device == "cuda" else "cpu"
    os.environ["KARAOKE_DEVICE"] = env_dev
    status.detail = "Gesangstrennung (ca. 80 MB)"
    _run([python_exe(), "-c", "from demucs.pretrained import get_model; get_model('htdemucs'); print('ok')"],
         "Download des Gesangstrennungs-Modells")
    status.step_fraction = 0.3
    status.detail = "Lyrics-Erkennung (" + ("ca. 1,5 GB" if env_dev == "cuda" else "ca. 460 MB") + ")"
    _run([python_exe(), "-c",
          f"import sys; sys.path.insert(0, r'{PIPELINE_DIR}'); import lyrics; lyrics._load_model(); print('ok')"],
         "Download des Lyrics-Modells")

    _step("check")
    for exe in (FF_DIR / "ffmpeg.exe", FF_DIR / "ffprobe.exe", DENO_DIR / "deno.exe", Path(python_exe())):
        if not exe.exists():
            raise RuntimeError(f"{exe.name} fehlt nach der Einrichtung.")
    INFO_FILE.write_text(json.dumps({"version": VERSION, "device": device, "gpu": gpu if device == "cuda" else None},
                                    ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    os.environ.pop("KARAOKE_DEVICE", None)


def _runner():
    try:
        _install()
        status.state = "done"
        status.add_log("Einrichtung abgeschlossen.")
    except Exception as e:
        status.state = "failed"
        status.error = str(e)
        status.add_log(f"FEHLER: {e}")


def start() -> dict:
    global _thread
    with status.lock:
        if status.state != "running":
            status.reset()
            status.state = "running"
            _thread = threading.Thread(target=_runner, daemon=True)
            _thread.start()
    return status.as_dict()


def run_blocking():
    start()
    _thread.join()
    return status.state == "done"
