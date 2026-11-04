"""Gemeinsame Pfade für die Desktop-App (Entwicklung und gebaute .exe)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
IS_WIN = os.name == "nt"

# In der .exe liegen app/ und pipeline/ im entpackten PyInstaller-Ordner
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
APP_DIR = BASE / "app"
PIPELINE_DIR = Path(os.environ.get("KARAOKE_PIPELINE_DIR", BASE / "pipeline"))


def _documents() -> Path:
    if IS_WIN:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            # CSIDL_PERSONAL = 5 → „Dokumente“ (auch wenn er in OneDrive liegt)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


LOCAL_APP = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share")) / "KaraokeStudio"
TOOLS = Path(os.environ.get("KARAOKE_TOOLS", LOCAL_APP / "tools"))
DATA = Path(os.environ.get("KARAOKE_DATA", _documents() / "Karaoke Studio"))
SETTINGS_FILE = LOCAL_APP / "settings.json"
LOG_FILE = LOCAL_APP / "log.txt"

NO_WINDOW = 0x08000000 if IS_WIN else 0   # subprocess.CREATE_NO_WINDOW

for _d in (LOCAL_APP, DATA / "songs", DATA / "videos", DATA / "inbox"):
    _d.mkdir(parents=True, exist_ok=True)
