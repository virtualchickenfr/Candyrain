"""Einen Song zu Karaoke verarbeiten.

  python pipeline/process.py --url "https://youtu.be/..."  [--artist X --title Y]
  python pipeline/process.py --file inbox/song.mp3         [--artist X --title Y]

Ergebnis in songs/<id>/:
  meta.json, lyrics.json, instrumental.mp3, vocals.mp3, cover.jpg (falls vorhanden)
und das Karaoke-Video unter --video (Standard: out/<id>.mp4).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lyrics as lyr  # noqa: E402
import render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"

_JUNK = re.compile(
    r"\s*[\(\[][^)\]]*(official|video|audio|lyric|lyrics|visualizer|hd|4k|remaster|clip|musikvideo)[^)\]]*[\)\]]",
    re.I)


def run(cmd: list[str]):
    print("$", " ".join(cmd[:6]), "…" if len(cmd) > 6 else "")
    subprocess.run(cmd, check=True)


def slugify(text: str, maxlen: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:maxlen].strip("-") or "song"


def clean_title(t: str) -> str:
    t = _JUNK.sub("", t)
    t = re.sub(r"\s*\|.*$", "", t)
    return t.strip(" -–")


def guess_artist_title(info: dict) -> tuple[str, str]:
    artist = info.get("artist") or info.get("creator") or ""
    title = info.get("track") or ""
    if artist and title:
        return artist.split(",")[0].strip(), clean_title(title)
    raw = clean_title(info.get("title") or "")
    for sep in (" - ", " – ", " — ", ": "):
        if sep in raw:
            a, t = raw.split(sep, 1)
            return a.strip(), clean_title(t)
    uploader = (info.get("uploader") or info.get("channel") or "").replace(" - Topic", "").replace("VEVO", "").strip()
    return uploader, raw


def fetch_youtube(url: str) -> tuple[Path, dict]:
    import yt_dlp
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(WORK / "source.%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    return path, info


def ffprobe_tags(path: Path) -> dict:
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format_tags",
                                       "-of", "json", str(path)])
        return {k.lower(): v for k, v in json.loads(out).get("format", {}).get("tags", {}).items()}
    except Exception:
        return {}


def download(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        return True
    except Exception as e:
        print(f"  Cover-Download fehlgeschlagen: {e}")
        return False


def device() -> str:
    """'cuda' wenn eine NVIDIA-Grafikkarte nutzbar ist, sonst 'cpu' (überschreibbar mit KARAOKE_DEVICE)."""
    forced = os.environ.get("KARAOKE_DEVICE")
    if forced:
        return forced
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def separate(wav: Path, work: Path) -> tuple[Path, Path]:
    dev = device()
    print(f"Trenne Gesang und Instrumental (Demucs, {dev.upper()}) …")
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-d", dev,
           "-o", str(work / "sep"), str(wav)]
    if dev == "cpu":
        cmd[8:8] = ["-j", str(max(1, (os.cpu_count() or 2)))]
    run(cmd)
    base = work / "sep" / "htdemucs" / wav.stem
    return base / "vocals.wav", base / "no_vocals.wav"


def process_song(url: str = "", file: str = "", artist: str = "", title: str = "", added_by: str = "",
                 data_dir: Path | None = None, video: Path | None = None, vocals: int = 0,
                 stage=lambda name: None) -> dict:
    """Verarbeitet einen Song komplett. Gibt die meta.json-Daten zurück (inkl. 'video')."""
    data_dir = Path(data_dir) if data_dir else ROOT
    work = data_dir / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    global WORK
    WORK = work

    # 1) Audio holen -------------------------------------------------------
    stage("Lade Audio")
    cover_url = None
    if url:
        print(f"Lade Audio von YouTube: {url}")
        src_path, info = fetch_youtube(url)
        g_artist, g_title = guess_artist_title(info)
        cover_url = info.get("thumbnail")
        source = {"type": "youtube", "url": url, "youtube_title": info.get("title")}
    else:
        src_path = Path(file) if Path(file).is_absolute() else data_dir / file
        tags = ffprobe_tags(src_path)
        g_artist, g_title = tags.get("artist", ""), tags.get("title", "")
        if not g_title:
            g_artist, g_title = guess_artist_title({"title": Path(file).stem.replace("_", " ")})
        source = {"type": "upload", "file": Path(file).name}

    artist = (artist or "").strip() or g_artist
    title = (title or "").strip() or g_title or "Unbekannter Song"
    print(f"Song: {artist} – {title}")

    wav = work / "source.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src_path),
         "-vn", "-ac", "2", "-ar", "44100", str(wav)])
    duration = render.probe_duration(wav)

    # 2) Gesang trennen ---------------------------------------------------
    stage("Trenne Gesang")
    vocals_wav, inst_wav = separate(wav, work)

    # 3) Lyrics -----------------------------------------------------------
    stage("Lyrics")
    lyrics = lyr.build_lyrics(str(vocals_wav), artist, title, duration)
    print(f"Lyrics: {len(lyrics['lines'])} Zeilen (Quelle: {lyrics['source']})")

    # 4) Speichern --------------------------------------------------------
    stage("Speichern")
    now = dt.datetime.now(dt.timezone.utc)
    song_id = f"{now:%Y%m%d-%H%M%S}-{slugify(f'{artist} {title}')}"
    out = data_dir / "songs" / song_id
    out.mkdir(parents=True, exist_ok=True)

    for wav_in, name in ((inst_wav, "instrumental.mp3"), (vocals_wav, "vocals.mp3")):
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav_in),
             "-c:a", "libmp3lame", "-b:a", "160k", str(out / name)])

    has_cover = False
    if cover_url and download(cover_url, work / "cover_src"):
        try:
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(work / "cover_src"),
                 "-vf", "scale=640:-2", "-q:v", "4", str(out / "cover.jpg")])
            has_cover = True
        except subprocess.CalledProcessError:
            pass

    meta = {
        "id": song_id,
        "artist": artist,
        "title": title,
        "duration": round(duration, 2),
        "added": now.isoformat(timespec="seconds"),
        "added_by": added_by,
        "source": source,
        "lyrics_source": lyrics["source"],
        "language": lyrics.get("language"),
        "has_cover": has_cover,
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "lyrics.json").write_text(json.dumps(lyrics, ensure_ascii=False), encoding="utf-8")

    # 5) Standard-Video ---------------------------------------------------
    stage("Video")
    video = Path(video) if video else data_dir / "videos" / song_id / f"{song_id}-gesang-{vocals}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    render.render(out, vocals, video)
    shutil.rmtree(work, ignore_errors=True)
    print(f"Fertig: {song_id}")
    return {**meta, "video": str(video)}


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url")
    src.add_argument("--file")
    ap.add_argument("--artist", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--added-by", default="")
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--video", default="")
    ap.add_argument("--vocals", type=int, default=0, help="Gesangslautstärke im Standard-Video")
    a = ap.parse_args()

    meta = process_song(url=a.url or "", file=a.file or "", artist=a.artist, title=a.title,
                        added_by=a.added_by, data_dir=Path(a.data_dir) if a.data_dir else None,
                        video=Path(a.video) if a.video else None, vocals=a.vocals,
                        stage=lambda name: print(f"@@STAGE {name}", flush=True))
    print("@@RESULT " + json.dumps({"song_id": meta["id"], "title": meta["title"], "artist": meta["artist"]},
                                   ensure_ascii=False), flush=True)

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            label = f"{meta['artist']} – {meta['title']}" if meta["artist"] else meta["title"]
            f.write(f"song_id={meta['id']}\nvideo={meta['video']}\nlabel={' '.join(label.split())}\n")


if __name__ == "__main__":
    main()
