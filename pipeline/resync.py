"""Lyrics-Timing und/oder SingStar-Noten eines vorhandenen Songs neu berechnen.

  python pipeline/resync.py songs/<id> [--lyrics] [--notes]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir")
    ap.add_argument("--lyrics", action="store_true")
    ap.add_argument("--notes", action="store_true")
    a = ap.parse_args()
    song = Path(a.song_dir)
    meta = json.loads((song / "meta.json").read_text(encoding="utf-8"))

    if a.notes:
        print("@@STAGE Noten", flush=True)
        import notes
        nd = notes.compute(song / "vocals.mp3")
        (song / "notes.json").write_text(json.dumps(nd, separators=(",", ":")), encoding="utf-8")
        meta["has_notes"] = True
        print(f"Noten: {len(nd['notes'])}")

    if a.lyrics:
        print("@@STAGE Lyrics", flush=True)
        import lyrics
        data = lyrics.build_lyrics(str(song / "vocals.mp3"), meta.get("artist", ""), meta.get("title", ""),
                                   float(meta.get("duration") or 0))
        (song / "lyrics.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        meta["lyrics_source"] = data["source"]
        print(f"Lyrics: {len(data['lines'])} Zeilen ({data['source']})")

    (song / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("@@RESULT " + json.dumps({"song_id": meta["id"]}), flush=True)


if __name__ == "__main__":
    main()
