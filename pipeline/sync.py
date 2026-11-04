"""Lyrics-Synchronisierung mit LRC-Ankern (nur numpy – unabhängig vom KI-Modell testbar).

Ablauf:
1. Gesangsaktivität aus der getrennten Gesangsspur bestimmen (Lautstärke pro 50 ms).
2. Globalen Versatz zwischen LRC-Zeiten und Audio suchen (z. B. YouTube-Intro vor dem Song).
3. Zeilen in Blöcken von max. ~24 s Wort für Wort ausrichten – jede Zeile bleibt an ihrer
   LRC-Zeit verankert, Fehler können sich nicht über den Song ausbreiten.
4. Zeilen, deren Ausrichtung unplausibel ist, einzeln neu ausrichten oder – als Notlösung –
   die Wörter über den tatsächlich gesungenen Bereich verteilen.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

SR = 16000
HOP = 0.05

AlignFn = Callable[[np.ndarray, list[str]], "list[list[dict]] | None"]


# ------------------------------------------------------------------ Gesangsaktivität

def vocal_activity(audio: np.ndarray, hop: float = HOP) -> np.ndarray:
    frame = int(SR * hop)
    n = len(audio) // frame
    if n == 0:
        return np.zeros(0, dtype=bool)
    x = audio[: n * frame].reshape(n, frame).astype(np.float32)
    rms = np.sqrt(np.mean(x * x, axis=1) + 1e-12)
    loud = np.percentile(rms, 95)
    thr = max(0.006, 0.18 * loud)
    active = rms > thr
    # kurze Lücken (< 0.3 s) schließen, einzelne Ausreißer (< 0.1 s) entfernen
    active = _close(active, int(0.3 / hop))
    active = _open(active, max(1, int(0.1 / hop)))
    return active


def _runs(a: np.ndarray):
    if a.size == 0:
        return []
    d = np.diff(np.concatenate([[0], a.astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _close(a: np.ndarray, n: int) -> np.ndarray:
    a = a.copy()
    gaps = _runs(~a)
    for s, e in gaps:
        if s > 0 and e < len(a) and e - s <= n:
            a[s:e] = True
    return a


def _open(a: np.ndarray, n: int) -> np.ndarray:
    a = a.copy()
    for s, e in _runs(a):
        if e - s < n:
            a[s:e] = False
    return a


# ------------------------------------------------------------------ Versatz

def _est_len(text: str) -> float:
    return 0.32 * len(text.split()) + 0.8


def lrc_entries(synced: list[tuple[float, str]], duration: float, is_section) -> list[dict]:
    """Zeilen mit Text + Zeit der nächsten LRC-Marke (auch leere Marken zählen als Grenze)."""
    out = []
    for i, (t, txt) in enumerate(synced):
        if not txt or is_section(txt):
            continue
        nxt = synced[i + 1][0] if i + 1 < len(synced) else duration
        out.append({"t": float(t), "next": float(max(nxt, t + 0.3)), "text": txt})
    return out


def estimate_offset(entries: list[dict], active: np.ndarray, hop: float = HOP,
                    search: float = 25.0) -> tuple[float, float, float]:
    """Gibt (versatz_sekunden, trefferquote_ohne, trefferquote_mit) zurück."""
    n = len(active)
    if n == 0 or not entries:
        return 0.0, 0.0, 0.0
    mask = np.zeros(n, dtype=np.float32)
    for e in entries:
        s = int(e["t"] / hop)
        en = int(min(e["next"], e["t"] + _est_len(e["text"])) / hop)
        mask[max(0, s):max(0, min(n, en))] = 1.0
    total = mask.sum()
    if total == 0:
        return 0.0, 0.0, 0.0
    a = active.astype(np.float32)
    K = int(search / hop)
    best_k, best = 0, -1.0
    scores = {}
    for k in range(-K, K + 1):
        if k >= 0:
            sc = float(np.dot(mask[: n - k], a[k:])) if k < n else 0.0
        else:
            sc = float(np.dot(mask[-k:], a[: n + k])) if -k < n else 0.0
        sc /= total
        scores[k] = sc
        if sc > best + 1e-9:
            best, best_k = sc, k
    zero = scores[0]
    # Nur verschieben, wenn es deutlich besser passt
    if best - zero < 0.06 or best < 0.35:
        return 0.0, zero, zero
    return best_k * hop, zero, best


def refine_offset(entries: list[dict], active: np.ndarray, offset: float, hop: float = HOP,
                  radius: float = 1.5) -> float:
    """Feinkorrektur: Median-Abstand der Zeilenanfänge zum nächsten Gesangseinsatz."""
    onsets = np.array([s * hop for s, _ in _runs(active)])
    if onsets.size == 0:
        return offset
    diffs = []
    for e in entries:
        t = e["t"] + offset
        near = onsets[np.abs(onsets - t) <= radius]
        if near.size:
            diffs.append(float(near[np.argmin(np.abs(near - t))] - t))
    if len(diffs) < max(3, len(entries) // 4):
        return offset
    corr = float(np.median(diffs))
    return offset + corr if abs(corr) > 0.08 else offset


# ------------------------------------------------------------------ Blöcke

def line_window(e: dict, duration: float) -> tuple[float, float]:
    ws = max(0.0, e["t"] - 0.6)
    we = min(e["next"] + 0.2, e["t"] + max(3.0, 0.6 * len(e["text"].split()) + 2.5), duration)
    return ws, max(we, min(duration, e["t"] + 1.0))


def plan_chunks(entries: list[dict], duration: float, max_len: float = 24.0, max_gap: float = 6.0) -> list[list[int]]:
    chunks, cur = [], []
    for i, e in enumerate(entries):
        if cur:
            ws0, _ = line_window(entries[cur[0]], duration)
            _, we_prev = line_window(entries[cur[-1]], duration)
            _, we = line_window(e, duration)
            if we - ws0 > max_len or e["t"] - we_prev > max_gap:
                chunks.append(cur)
                cur = []
        cur.append(i)
    if cur:
        chunks.append(cur)
    return chunks


# ------------------------------------------------------------------ Prüfung & Notlösung

def _valid(words: list[dict], e: dict, duration: float) -> bool:
    if not words:
        return False
    ws, we = line_window(e, duration)
    s, en = words[0]["s"], words[-1]["e"]
    if s < e["t"] - 1.0 or s > e["t"] + 1.6:
        return False
    if en > we + 0.6 or en <= s:
        return False
    zero = sum(1 for w in words if w["e"] - w["s"] < 0.02)
    return zero <= max(1, len(words) // 3)


def distribute(e: dict, active: np.ndarray, duration: float, hop: float = HOP) -> list[dict]:
    """Wörter über den gesungenen Bereich nach der LRC-Zeit verteilen."""
    ws, we = line_window(e, duration)
    start = e["t"]
    i0, i1 = int(max(0, start - 0.3) / hop), int(we / hop)
    seg = active[i0:i1] if i1 > i0 else np.zeros(0, dtype=bool)
    idx = np.where(seg)[0]
    if idx.size:
        s = max(start - 0.3, (i0 + idx[0]) * hop)
        en = min(we, (i0 + idx[-1] + 1) * hop)
        # nicht über die nächste Zeile hinaus und nicht absurd lang
        en = min(en, s + _est_len(e["text"]) * 1.8)
    else:
        s, en = start, min(we, start + _est_len(e["text"]))
    if en - s < 0.3:
        en = s + max(0.3, _est_len(e["text"]))
    toks = e["text"].split()
    weights = np.array([max(1, len(w)) for w in toks], dtype=float)
    edges = s + (en - s) * np.concatenate([[0], np.cumsum(weights) / weights.sum()])
    return [{"t": w, "s": round(float(edges[k]), 3), "e": round(float(edges[k + 1]) - 0.02, 3)}
            for k, w in enumerate(toks)]


def _finish(lines: list[dict]) -> list[dict]:
    """Reihenfolge sichern, Überlappungen glätten, Mindestdauer pro Wort."""
    prev_end = 0.0
    for ln in lines:
        for w in ln["words"]:
            w["s"] = max(w["s"], prev_end - 0.05)
            w["e"] = max(w["e"], w["s"] + 0.06)
            prev_end = w["e"]
        ln["start"], ln["end"] = ln["words"][0]["s"], ln["words"][-1]["e"]
    for a, b in zip(lines, lines[1:]):
        if a["end"] > b["start"]:
            cut = b["start"] - 0.02
            for w in a["words"]:
                w["e"] = min(w["e"], cut)
                w["s"] = min(w["s"], w["e"] - 0.04)
            a["end"] = a["words"][-1]["e"]
    for ln in lines:
        for w in ln["words"]:
            w["s"], w["e"] = round(w["s"], 3), round(w["e"], 3)
        ln["start"], ln["end"] = round(ln["start"], 3), round(ln["end"], 3)
    return lines


# ------------------------------------------------------------------ Hauptfunktion

def sync_lines(audio: np.ndarray, synced: list[tuple[float, str]], duration: float,
               align_fn: AlignFn, is_section=lambda t: False, log=print) -> tuple[list[dict], dict]:
    active = vocal_activity(audio)
    entries = lrc_entries(synced, duration, is_section)
    offset, before, after = estimate_offset(entries, active)
    offset = refine_offset(entries, active, offset)
    log(f"  Gesang trifft LRC-Zeiten: {before:.0%} → Versatz {offset:+.2f}s ({after:.0%})")
    for e in entries:
        e["t"] += offset
        e["next"] += offset

    result: list[list[dict] | None] = [None] * len(entries)
    stats = {"offset": round(offset, 2), "chunk": 0, "single": 0, "fallback": 0, "lines": len(entries)}

    def run_align(idxs: list[int]) -> list[list[dict]] | None:
        ws = line_window(entries[idxs[0]], duration)[0]
        we = line_window(entries[idxs[-1]], duration)[1]
        seg = audio[int(ws * SR): int(we * SR)]
        if len(seg) < SR // 4:
            return None
        try:
            res = align_fn(seg, [entries[i]["text"] for i in idxs])
        except Exception as ex:      # Modell-Fehler → Notlösung
            log(f"  Ausrichtung fehlgeschlagen ({ex})")
            return None
        if not res or len(res) != len(idxs):
            return None
        return [[{"t": w["t"], "s": w["s"] + ws, "e": w["e"] + ws} for w in words] for words in res]

    for chunk in plan_chunks(entries, duration):
        res = run_align(chunk)
        for j, i in enumerate(chunk):
            if res and _valid(res[j], entries[i], duration):
                result[i] = res[j]
                stats["chunk"] += 1

    for i, e in enumerate(entries):
        if result[i] is None:
            res = run_align([i])
            if res and _valid(res[0], e, duration):
                result[i] = res[0]
                stats["single"] += 1
            else:
                result[i] = distribute(e, active, duration)
                stats["fallback"] += 1

    lines = [{"start": w[0]["s"], "end": w[-1]["e"], "text": e["text"], "words": w}
             for e, w in zip(entries, result) if w]
    log(f"  Zeilen: {stats['chunk']} im Block, {stats['single']} einzeln, {stats['fallback']} geschätzt")
    return _finish(lines), stats
