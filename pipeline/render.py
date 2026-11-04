"""Karaoke-Video rendern: Instrumental + (optional) leiser Gesang + Lyrics mit Wort-Highlighting.

Aufruf:  python pipeline/render.py songs/<id> --vocals 20 --out video.mp4
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

W, H = 1280, 720
FONT_DIR = Path(__file__).parent / "fonts"
FONT = "Poppins"

# ASS-Farben sind &HAABBGGRR
SUNG = "&H0040D8FF"      # warmes Gelb/Orange (gesungen)
UNSUNG = "&H00FFFFFF"    # weiß (noch nicht gesungen)
OUTLINE = "&H00000000"
SHADOW = "&H80000000"

ROW_Y = (H // 2 - 50, H // 2 + 60)   # zwei Zeilen untereinander (ohne Noten)
ROW_Y_NOTES = (H // 2 + 30, H // 2 + 130)  # mit Notenbahn oben etwas tiefer
LANE = (110, 70, W - 110, 270)       # Notenbahn: x0, y0, x1, y1
LEAD_IN = 2.2                        # Sekunden, die eine Zeile vorher erscheint
HOLD = 0.6                           # Sekunden, die sie nach dem Singen stehen bleibt


def ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def shift_lyrics(lyrics: dict, offset: float) -> dict:
    if not offset:
        return lyrics
    lines = []
    for ln in lyrics.get("lines", []):
        words = [{**w, "s": w["s"] + offset, "e": w["e"] + offset} for w in ln["words"]]
        lines.append({**ln, "start": ln["start"] + offset, "end": ln["end"] + offset, "words": words})
    return {**lyrics, "lines": lines}


def note_pages(lines: list[dict], notes: list, duration: float) -> list[tuple[float, float]]:
    """Seiten wie bei SingStar: pro Lyrics-Zeile eine Seite, ohne Lyrics 6-s-Abschnitte."""
    if lines:
        return [(ln["start"] - 0.25, ln["end"] + 0.25) for ln in lines]
    pages, t = [], 0.0
    while t < duration:
        pages.append((t, min(duration, t + 6.0)))
        t += 6.0
    return pages


def _rect(w: float, h: float) -> str:
    w, h = max(1, round(w)), max(1, round(h))
    return f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"


def notes_events(lines: list[dict], notes: list, duration: float) -> list[str]:
    if not notes:
        return []
    x0, y0, x1, y1 = LANE
    ev = []
    pages = note_pages(lines, notes, duration)
    for i, (p0, p1) in enumerate(pages):
        page_notes = [n for n in notes if n[1] > p0 and n[0] < p1]
        if not page_notes:
            continue
        show = max(p0 - 1.2, pages[i - 1][1] if i else 0.0, 0.0)
        hide = min(p1 + 0.15, pages[i + 1][0] - 1.2 if i + 1 < len(pages) else p1 + 0.5)
        hide = max(hide, p1)
        if show >= hide:
            continue
        lo = min(n[2] for n in page_notes)
        hi = max(n[2] for n in page_notes)
        span = max(12, hi - lo + 2)
        mid = (lo + hi) / 2
        base = mid - span / 2
        row_h = (y1 - y0) / span
        bar_h = max(8.0, row_h * 0.85)
        sx = (x1 - x0) / max(0.5, p1 - p0)
        # Hintergrund der Bahn
        ev.append(f"Dialogue: 0,{ts(show)},{ts(hide)},Lane,,0,0,0,,"
                  f"{{\\an7\\pos({x0 - 20},{y0 - 16})\\fad(250,250)\\p1}}{_rect(x1 - x0 + 40, y1 - y0 + 32)}{{\\p0}}")
        for s_, e_, m in page_notes:
            s_, e_ = max(s_, p0), min(e_, p1)
            nx0 = x0 + (s_ - p0) * sx
            nw = max(6.0, (e_ - s_) * sx)
            ny = y1 - (m - base) * row_h - bar_h / 2
            pos = f"\\an7\\pos({nx0:.0f},{ny:.0f})"
            ev.append(f"Dialogue: 1,{ts(show)},{ts(hide)},NoteOff,,0,0,0,,{{{pos}\\fad(250,250)\\p1}}{_rect(nw, bar_h)}{{\\p0}}")
            cx0, cy0, cy1 = int(nx0) - 1, int(ny) - 4, int(ny + bar_h) + 4
            cx1 = int(nx0 + nw) + 1
            if s_ < hide:   # Gold-Füllung erst ab Notenbeginn, wächst mit der Zeit nach rechts
                ev.append(f"Dialogue: 2,{ts(s_)},{ts(hide)},NoteOn,,0,0,0,,"
                          f"{{{pos}\\fad(0,250)\\clip({cx0},{cy0},{cx0 + 2},{cy1})"
                          f"\\t(0,{int((e_ - s_) * 1000)},\\clip({cx0},{cy0},{cx1},{cy1}))\\p1}}{_rect(nw, bar_h)}{{\\p0}}")
        # wandernder Balken (Abspielposition)
        ev.append(f"Dialogue: 3,{ts(p0)},{ts(p1)},Playhead,,0,0,0,,"
                  f"{{\\an7\\move({x0},{y0 - 8},{x1},{y0 - 8})\\p1}}{_rect(3, y1 - y0 + 16)}{{\\p0}}")
    return ev


def build_ass(lyrics: dict, meta: dict, duration: float, notes: list | None = None) -> str:
    lines = lyrics.get("lines", [])
    row_y = ROW_Y_NOTES if notes else ROW_Y
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lyric,{FONT},54,{SUNG},{UNSUNG},{OUTLINE},{SHADOW},-1,0,0,0,100,100,1,0,1,3.5,2,5,60,60,0,1
Style: Title,{FONT},64,&H00FFFFFF,&H00FFFFFF,{OUTLINE},{SHADOW},-1,0,0,0,100,100,1,0,1,3,2,5,60,60,0,1
Style: Artist,{FONT},38,&H00D0D0D0,&H00D0D0D0,{OUTLINE},{SHADOW},0,0,0,0,100,100,1,0,1,2,1,5,60,60,0,1
Style: Dots,{FONT},60,{SUNG},{SUNG},{OUTLINE},{SHADOW},-1,0,0,0,100,100,8,0,1,3,1,5,60,60,0,1
Style: Small,{FONT},26,&H00B0B0B0,&H00B0B0B0,{OUTLINE},{SHADOW},0,0,0,0,100,100,0,0,1,1.5,1,2,40,40,24,1
Style: Lane,{FONT},20,&HA0140A1C,&HA0140A1C,&HFF000000,&HFF000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: NoteOff,{FONT},20,&HB8FFFFFF,&HB8FFFFFF,&H50FFFFFF,&HFF000000,0,0,0,0,100,100,0,0,1,1.5,0,7,0,0,0,1
Style: NoteOn,{FONT},20,{SUNG},{SUNG},&H0000A0FF,&HFF000000,0,0,0,0,100,100,0,0,1,1.5,0,7,0,0,0,1
Style: Playhead,{FONT},20,&H30FFFFFF,&H30FFFFFF,&HFF000000,&HFF000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    title = esc(meta.get("title") or "Karaoke")
    artist = esc(meta.get("artist") or "")
    first = lines[0]["start"] if lines else duration

    # Titelkarte am Anfang
    card_end = min(max(3.0, first - LEAD_IN - 0.3), 8.0) if lines else min(duration, 8.0)
    if card_end > 1.0:
        ev.append(f"Dialogue: 1,{ts(0)},{ts(card_end)},Title,,0,0,0,,{{\\pos({W//2},{H//2 - 30})\\fad(400,400)}}{title}")
        if artist:
            ev.append(f"Dialogue: 1,{ts(0)},{ts(card_end)},Artist,,0,0,0,,{{\\pos({W//2},{H//2 + 40})\\fad(400,400)}}{artist}")

    # Countdown-Punkte vor dem ersten Einsatz und nach langen Pausen
    def countdown(at: float):
        if at < 3.2:
            return
        for i, n in enumerate((3, 2, 1)):
            s = at - 3 + i
            ev.append(f"Dialogue: 2,{ts(s)},{ts(s + 1)},Dots,,0,0,0,,{{\\pos({W//2},{row_y[0] - 90})}}" + "● " * n)

    if lines:
        countdown(first)

    # Zeilen abwechselnd oben/unten
    row_free = [0.0, 0.0]
    for i, ln in enumerate(lines):
        row = i % 2
        prev_end = lines[i - 1]["end"] if i > 0 else 0.0
        if i > 0 and ln["start"] - prev_end > 8:
            countdown(ln["start"])
        show = min(max(row_free[row] + 0.05, ln["start"] - LEAD_IN, 0.0), ln["start"])
        nxt_same = lines[i + 2]["start"] - LEAD_IN if i + 2 < len(lines) else ln["end"] + HOLD + 2
        hide = min(ln["end"] + HOLD, max(nxt_same - 0.05, ln["end"] + 0.1))
        row_free[row] = hide

        parts, cursor = [], show
        for w in ln["words"]:
            gap = w["s"] - cursor
            if gap > 0.01:
                parts.append(f"{{\\k{int(round(gap * 100))}}}")
            dur = max(0.05, w["e"] - max(w["s"], cursor))
            parts.append(f"{{\\kf{int(round(dur * 100))}}}{esc(w['t'])} ")
            cursor = max(cursor, w["s"]) + dur
        text = "".join(parts).rstrip()
        ev.append(f"Dialogue: 0,{ts(show)},{ts(hide)},Lyric,,0,0,0,,{{\\pos({W//2},{row_y[row]})\\fad(200,250)}}{text}")

    if not lines:
        ev.append(f"Dialogue: 0,{ts(card_end)},{ts(duration)},Artist,,0,0,0,,{{\\pos({W//2},{H//2})}}(keine Lyrics gefunden)")

    ev.append(f"Dialogue: 0,{ts(card_end)},{ts(duration)},Small,,0,0,0,,{title}" + (f"  ·  {artist}" if artist else ""))
    ev += notes_events(lines, notes or [], duration)
    return head + "\n".join(ev) + "\n"


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nw=1:nk=1", str(path)])
    return float(out.decode().strip())


def render(song_dir: Path, vocals_pct: int, out: Path, offset: float = 0.0, with_notes: bool = True) -> Path:
    song_dir = Path(song_dir)
    meta = json.loads((song_dir / "meta.json").read_text(encoding="utf-8"))
    lyrics = shift_lyrics(json.loads((song_dir / "lyrics.json").read_text(encoding="utf-8")), offset)
    notes = None
    if with_notes and (song_dir / "notes.json").exists():
        try:
            notes = json.loads((song_dir / "notes.json").read_text(encoding="utf-8")).get("notes") or None
        except Exception:
            notes = None
    inst, voc = song_dir / "instrumental.mp3", song_dir / "vocals.mp3"
    duration = meta.get("duration") or probe_duration(inst)

    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    # Untertitel + Schrift in einen Temp-Ordner und ffmpeg dort starten:
    # so braucht der Filter keine (unter Windows problematischen) absoluten Pfade.
    tmp = Path(tempfile.mkdtemp(prefix="karaoke-render-"))
    try:
        (tmp / "lyrics.ass").write_text(build_ass(lyrics, meta, duration, notes), encoding="utf-8")
        fonts = tmp / "fonts"
        fonts.mkdir()
        for f in FONT_DIR.glob("*.ttf"):
            shutil.copy(f, fonts / f.name)

        cover = song_dir / "cover.jpg"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats"]
        if cover.exists():
            cmd += ["-loop", "1", "-framerate", "25", "-i", str(cover.resolve())]
            bg = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"boxblur=24:3,eq=brightness=-0.32:saturation=1.15,format=yuv420p")
        else:
            cmd += ["-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0=0x1b1035:c1=0x3a1450:c2=0x0d2a4a:x0=0:y0=0:x1={W}:y1={H}:speed=0.004:r=25"]
            bg = "[0:v]format=yuv420p"
        cmd += ["-i", str(inst.resolve()), "-i", str(voc.resolve())]

        vol = max(0, min(100, vocals_pct)) / 100
        filt = (f"{bg},ass=lyrics.ass:fontsdir=fonts[v];"
                f"[2:a]volume={vol:.2f}[voc];[1:a][voc]amix=inputs=2:normalize=0:duration=first,"
                f"alimiter=limit=0.97[a]")
        enc = os.environ.get("KARAOKE_VIDEO_ENCODER", "libx264")
        venc = ["-c:v", enc] + (["-preset", "veryfast", "-crf", "26", "-tune", "stillimage"] if enc == "libx264"
                                else ["-preset", "p4", "-cq", "28"])
        cmd += ["-filter_complex", filt, "-map", "[v]", "-map", "[a]",
                "-t", f"{duration:.2f}", "-r", "25", *venc,
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
        print("Rendere Video …")
        subprocess.run(cmd, check=True, cwd=tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_dir")
    ap.add_argument("--vocals", type=int, default=0, help="Gesangslautstärke 0–100 %%")
    ap.add_argument("--out", required=True)
    ap.add_argument("--offset", type=float, default=0.0, help="Lyrics um so viele Sekunden verschieben")
    ap.add_argument("--no-notes", action="store_true")
    a = ap.parse_args()
    render(Path(a.song_dir), a.vocals, Path(a.out), offset=a.offset, with_notes=not a.no_notes)


if __name__ == "__main__":
    main()
