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


def separate(wav: Path) -> tuple[Path, Path]:
    print("Trenne Gesang und Instrumental (Demucs) …")
    jobs = str(max(1, (os.cpu_count() or 2)))
    run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-d", "cpu",
         "-j", jobs, "-o", str(WORK / "sep"), str(wav)])
    base = WORK / "sep" / "htdemucs" / wav.stem
    return base / "vocals.wav", base / "no_vocals.wav"


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url")
    src.add_argument("--file")
    ap.add_argument("--artist", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--added-by", default="")
    ap.add_argument("--video", default="")
    ap.add_argument("--vocals", type=int, default=0, help="Gesangslautstärke im Standard-Video")
    a = ap.parse_args()

    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    # 1) Audio holen -------------------------------------------------------
    cover_url = None
    if a.url:
        print(f"Lade Audio von YouTube: {a.url}")
        src_path, info = fetch_youtube(a.url)
        artist, title = guess_artist_title(info)
        cover_url = info.get("thumbnail")
        source = {"type": "youtube", "url": a.url, "youtube_title": info.get("title")}
    else:
        src_path = ROOT / a.file
        tags = ffprobe_tags(src_path)
        artist = tags.get("artist", "")
        title = tags.get("title", "")
        if not title:
            artist, title = guess_artist_title({"title": Path(a.file).stem.replace("_", " ")})
        source = {"type": "upload", "file": Path(a.file).name}

    artist = a.artist.strip() or artist
    title = a.title.strip() or title or "Unbekannter Song"
    print(f"Song: {artist} – {title}")

    wav = WORK / "source.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src_path),
         "-vn", "-ac", "2", "-ar", "44100", str(wav)])
    duration = render.probe_duration(wav)

    # 2) Gesang trennen ---------------------------------------------------
    vocals_wav, inst_wav = separate(wav)

    # 3) Lyrics -----------------------------------------------------------
    lyrics = lyr.build_lyrics(str(vocals_wav), artist, title, duration)
    print(f"Lyrics: {len(lyrics['lines'])} Zeilen (Quelle: {lyrics['source']})")

    # 4) Speichern --------------------------------------------------------
    now = dt.datetime.now(dt.timezone.utc)
    song_id = f"{now:%Y%m%d-%H%M%S}-{slugify(f'{artist} {title}')}"
    out = ROOT / "songs" / song_id
    out.mkdir(parents=True, exist_ok=True)

    for wav_in, name in ((inst_wav, "instrumental.mp3"), (vocals_wav, "vocals.mp3")):
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav_in),
             "-c:a", "libmp3lame", "-b:a", "160k", str(out / name)])

    has_cover = False
    if cover_url and download(cover_url, WORK / "cover_src"):
        try:
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(WORK / "cover_src"),
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
        "added_by": a.added_by,
        "source": source,
        "lyrics_source": lyrics["source"],
        "language": lyrics.get("language"),
        "has_cover": has_cover,
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "lyrics.json").write_text(json.dumps(lyrics, ensure_ascii=False), encoding="utf-8")

    # 5) Standard-Video ---------------------------------------------------
    video = Path(a.video) if a.video else ROOT / "out" / f"{song_id}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    render.render(out, a.vocals, video)

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            label = f"{artist} – {title}" if artist else title
            f.write(f"song_id={song_id}\nvideo={video}\nlabel={' '.join(label.split())}\n")
    print(f"Fertig: {song_id}")


if __name__ == "__main__":
    main()
