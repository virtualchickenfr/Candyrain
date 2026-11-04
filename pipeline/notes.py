"""SingStar-Noten: Tonhöhe der Gesangsspur → Notenbalken (nur zur Anzeige).

Tonhöhe per YIN-Verfahren (nur numpy), dann zu Noten in Halbtönen zusammengefasst.
Aufruf:  python pipeline/notes.py songs/<id>      → schreibt songs/<id>/notes.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 16000
HOP = 160            # 10 ms
WIN = 1024           # 64 ms
FMIN, FMAX = 70.0, 1000.0


def load_audio(path: str | Path) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-ac", "1",
                          "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True)
    return np.frombuffer(out.stdout, dtype=np.float32).copy()


def yin(audio: np.ndarray, threshold: float = 0.15, block: int = 2000):
    """Gibt (zeiten, frequenz_hz, klarheit, lautstärke) pro 10 ms zurück; frequenz 0 = stumm."""
    tau_min, tau_max = int(SR / FMAX), int(SR / FMIN)
    n = max(0, (len(audio) - WIN) // HOP + 1)
    f0 = np.zeros(n, dtype=np.float32)
    clar = np.zeros(n, dtype=np.float32)
    rms = np.zeros(n, dtype=np.float32)
    idx = np.arange(WIN)[None, :]
    nfft = 2 * WIN
    for b0 in range(0, n, block):
        b1 = min(n, b0 + block)
        frames = audio[(np.arange(b0, b1)[:, None] * HOP) + idx]
        frames = frames - frames.mean(axis=1, keepdims=True)
        rms[b0:b1] = np.sqrt((frames ** 2).mean(axis=1))
        # Differenzfunktion über Autokorrelation (FFT)
        spec = np.fft.rfft(frames, nfft, axis=1)
        acf = np.fft.irfft(spec * np.conj(spec), nfft, axis=1)[:, :tau_max + 2]
        energy = np.cumsum(frames[:, ::-1] ** 2, axis=1)[:, ::-1]   # Energie ab Position j
        e0 = acf[:, 0:1]
        e_tau = energy[:, :tau_max + 2]
        d = e0 + e_tau - 2 * acf
        d[:, 0] = 0
        cum = np.cumsum(d[:, 1:], axis=1)
        taus = np.arange(1, d.shape[1])
        cmnd = np.ones_like(d)
        cmnd[:, 1:] = d[:, 1:] * taus / np.maximum(cum, 1e-12)
        for k in range(b1 - b0):
            row = cmnd[k]
            cand = np.where(row[tau_min:tau_max] < threshold)[0]
            if cand.size:
                t = cand[0] + tau_min
                while t + 1 < tau_max and row[t + 1] < row[t]:
                    t += 1
            else:
                t = int(np.argmin(row[tau_min:tau_max])) + tau_min
                if row[t] > 0.35:
                    continue
            # parabolische Verfeinerung
            ti = int(t)
            tf = float(ti)
            if 1 <= ti < len(row) - 1:
                a, bb, c = row[ti - 1], row[ti], row[ti + 1]
                den = a - 2 * bb + c
                if abs(den) > 1e-9:
                    sh = 0.5 * (a - c) / den
                    if abs(sh) <= 1:
                        tf = ti + sh
            f0[b0 + k] = SR / tf
            clar[b0 + k] = 1.0 - float(row[ti])
    times = (np.arange(n) * HOP + WIN / 2) / SR
    return times, f0, clar, rms


def _median(x: np.ndarray, k: int) -> np.ndarray:
    if len(x) < k:
        return x
    pad = k // 2
    xp = np.pad(x, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(xp, k)
    return np.median(win, axis=1)


def to_notes(times, f0, clar, rms, min_len: float = 0.09, max_jump: float = 0.8) -> list[list[float]]:
    loud = np.percentile(rms[rms > 0], 95) if np.any(rms > 0) else 0
    voiced = (f0 > 0) & (clar > 0.55) & (rms > max(0.004, 0.12 * loud))
    midi = np.full(len(f0), np.nan, dtype=np.float32)
    midi[voiced] = 69 + 12 * np.log2(f0[voiced] / 440.0)
    # Oktavsprünge glätten + Median
    filled = np.where(np.isnan(midi), 0, midi)
    smooth = _median(filled, 7)
    smooth[~voiced] = np.nan
    notes = []
    start, cur = None, []
    hop = times[1] - times[0] if len(times) > 1 else 0.01

    def flush(end_i):
        if start is None or not cur:
            return
        dur = times[end_i - 1] - times[start] + hop
        if dur >= min_len:
            notes.append([round(float(times[start] - hop / 2), 3), round(float(times[start] - hop / 2 + dur), 3),
                          int(round(float(np.median(cur))))])

    for i, m in enumerate(smooth):
        if np.isnan(m):
            flush(i)
            start, cur = None, []
            continue
        if start is not None and abs(m - np.median(cur)) > max_jump:
            flush(i)
            start, cur = None, []
        if start is None:
            start = i
        cur.append(m)
    flush(len(smooth))

    # Nachbarn mit gleicher Tonhöhe und winziger Lücke verbinden
    merged = []
    for n in notes:
        if merged and n[2] == merged[-1][2] and n[0] - merged[-1][1] < 0.06:
            merged[-1][1] = n[1]
        else:
            merged.append(n)
    return merged


def compute(vocals_path: str | Path) -> dict:
    audio = load_audio(vocals_path)
    times, f0, clar, rms = yin(audio)
    notes = to_notes(times, f0, clar, rms)
    pitches = [n[2] for n in notes]
    rng = [int(np.floor(np.percentile(pitches, 3))), int(np.ceil(np.percentile(pitches, 97)))] if pitches else [48, 72]
    return {"version": 1, "notes": notes, "range": rng}


def main():
    song = Path(sys.argv[1])
    data = compute(song / "vocals.mp3")
    (song / "notes.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"Noten: {len(data['notes'])} (Bereich MIDI {data['range'][0]}–{data['range'][1]})")


if __name__ == "__main__":
    main()
