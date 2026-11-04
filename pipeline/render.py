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

ROW_Y = (H // 2 - 50, H // 2 + 60)   # zwei Zeilen untereinander
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


def build_ass(lyrics: dict, meta: dict, duration: float) -> str:
    lines = lyrics.get("lines", [])
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
            ev.append(f"Dialogue: 2,{ts(s)},{ts(s + 1)},Dots,,0,0,0,,{{\\pos({W//2},{ROW_Y[0] - 90})}}" + "● " * n)

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
        ev.append(f"Dialogue: 0,{ts(show)},{ts(hide)},Lyric,,0,0,0,,{{\\pos({W//2},{ROW_Y[row]})\\fad(200,250)}}{text}")

    if not lines:
        ev.append(f"Dialogue: 0,{ts(card_end)},{ts(duration)},Artist,,0,0,0,,{{\\pos({W//2},{H//2})}}(keine Lyrics gefunden)")

    ev.append(f"Dialogue: 0,{ts(card_end)},{ts(duration)},Small,,0,0,0,,{title}" + (f"  ·  {artist}" if artist else ""))
    return head + "\n".join(ev) + "\n"


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nw=1:nk=1", str(path)])
    return float(out.decode().strip())


def render(song_dir: Path, vocals_pct: int, out: Path) -> Path:
    song_dir = Path(song_dir)
    meta = json.loads((song_dir / "meta.json").read_text(encoding="utf-8"))
    lyrics = json.loads((song_dir / "lyrics.json").read_text(encoding="utf-8"))
    inst, voc = song_dir / "instrumental.mp3", song_dir / "vocals.mp3"
    duration = meta.get("duration") or probe_duration(inst)

    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    # Untertitel + Schrift in einen Temp-Ordner und ffmpeg dort starten:
    # so braucht der Filter keine (unter Windows problematischen) absoluten Pfade.
    tmp = Path(tempfile.mkdtemp(prefix="karaoke-render-"))
    try:
        (tmp / "lyrics.ass").write_text(build_ass(lyrics, meta, duration), encoding="utf-8")
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
    a = ap.parse_args()
    render(Path(a.song_dir), a.vocals, Path(a.out))


if __name__ == "__main__":
    main()
