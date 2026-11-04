"""Lyrics holen (LRCLIB) und Wort für Wort am Gesang ausrichten (stable-ts / Whisper)."""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

LRCLIB = "https://lrclib.net/api"
USER_AGENT = "karaoke-studio/1.0 (private family project)"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")

_SECTION = re.compile(r"^\s*[\[(](chorus|refrain|verse|strophe|bridge|intro|outro|hook|pre-chorus|instrumental)[^\])]*[\])]\s*$", re.I)
_LRC_TIME = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


# --------------------------------------------------------------------------- LRCLIB

def _get(path: str, params: dict) -> object | None:
    url = f"{LRCLIB}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # 404 = nicht gefunden, alles andere: einfach weitermachen
        print(f"  LRCLIB {path}: {e}")
        return None


def _usable(c: dict) -> bool:
    """Aussortieren von kaputten/leeren Einträgen (z. B. nur ein Testwort)."""
    if c.get("instrumental"):
        return True
    lines = clean_lines(c.get("plainLyrics") or "")
    words = sum(len(l.split()) for l in lines)
    return len(lines) >= 6 and words >= 30


def find_lyrics(artist: str, title: str, duration: float) -> dict | None:
    """Sucht passende Lyrics. Gibt das beste LRCLIB-Objekt zurück oder None."""
    candidates: dict[int, dict] = {}

    def add(items):
        for c in items or []:
            if isinstance(c, dict) and c.get("id") is not None:
                candidates[c["id"]] = c

    if title:
        hit = _get("get", {"artist_name": artist or "", "track_name": title, "duration": round(duration)})
        if isinstance(hit, dict):
            add([hit])
        add(_get("search", {"track_name": title, "artist_name": artist} if artist else {"track_name": title}))
    add(_get("search", {"q": f"{artist} {title}".strip()}))

    usable = [c for c in candidates.values() if _usable(c)]
    print(f"  LRCLIB: {len(candidates)} Treffer, {len(usable)} brauchbar")

    best, best_score = None, 1e9
    for c in usable:
        diff = abs((c.get("duration") or 0) - duration)
        if diff > 12:
            continue
        n_lines = len(clean_lines(c.get("plainLyrics") or ""))
        score = diff - (3 if c.get("syncedLyrics") else 0) - min(n_lines, 60) * 0.03
        if score < best_score:
            best, best_score = c, score
    return best


def clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _SECTION.match(line):
            continue
        lines.append(line)
    return lines


def parse_lrc(lrc: str) -> list[tuple[float, str]]:
    out = []
    for raw in lrc.splitlines():
        stamps = _LRC_TIME.findall(raw)
        text = _LRC_TIME.sub("", raw).strip()
        for m, s in stamps:
            out.append((int(m) * 60 + float(s), text))
    out.sort(key=lambda x: x[0])
    return out


# --------------------------------------------------------------------------- Whisper

_model = None


def _load_model():
    global _model
    if _model is None:
        import stable_whisper
        print(f"  Lade Whisper-Modell '{WHISPER_MODEL}' …")
        _model = stable_whisper.load_model(WHISPER_MODEL, device="cpu")
    return _model


def detect_language(vocals_path: str, at: float = 30.0) -> str:
    import whisper
    model = _load_model()
    audio = whisper.load_audio(vocals_path)
    start = int(max(0.0, at) * 16000)
    clip = audio[start:start + 30 * 16000]
    if len(clip) < 16000 * 5:
        clip = audio[: 30 * 16000]
    mel = whisper.log_mel_spectrogram(whisper.pad_or_trim(clip), model.dims.n_mels).to(model.device)
    _, probs = model.detect_language(mel)
    lang = max(probs, key=probs.get)
    print(f"  Erkannte Sprache: {lang}")
    return lang


def _result_to_lines(result) -> list[dict]:
    lines = []
    for seg in result.segments:
        words = []
        for w in seg.words or []:
            t = w.word.strip()
            if not t:
                continue
            words.append({"t": t, "s": round(float(w.start), 3), "e": round(float(w.end), 3)})
        if not words:
            continue
        lines.append({
            "start": words[0]["s"],
            "end": words[-1]["e"],
            "text": " ".join(w["t"] for w in words),
            "words": words,
        })
    return lines


def _split_long(lines: list[dict], max_words: int = 9) -> list[dict]:
    """Sehr lange Zeilen (z. B. aus der Transkription) in zwei Hälften teilen."""
    out = []
    for ln in lines:
        words = ln["words"]
        if len(words) <= max_words:
            out.append(ln)
            continue
        n = (len(words) + max_words - 1) // max_words
        size = (len(words) + n - 1) // n
        for i in range(0, len(words), size):
            part = words[i:i + size]
            out.append({"start": part[0]["s"], "end": part[-1]["e"],
                        "text": " ".join(w["t"] for w in part), "words": part})
    return out


def lines_from_lrc(synced: list[tuple[float, str]], duration: float) -> list[dict]:
    """Notlösung: Zeilen-Timing aus LRC, Wörter gleichmäßig in der Zeile verteilt."""
    lines = []
    for i, (t, text) in enumerate(synced):
        if not text or _SECTION.match(text):
            continue
        nxt = synced[i + 1][0] if i + 1 < len(synced) else min(duration, t + 6)
        end = min(nxt - 0.1, t + max(1.5, 0.45 * len(text.split()) + 1.0))
        toks = text.split()
        span = max(0.3, end - t)
        weights = [max(1, len(w)) for w in toks]
        total = sum(weights)
        pos, words = t, []
        for w, wt in zip(toks, weights):
            d = span * wt / total
            words.append({"t": w, "s": round(pos, 3), "e": round(pos + d, 3)})
            pos += d
        lines.append({"start": round(t, 3), "end": round(end, 3), "text": text, "words": words})
    return lines


def _alignment_ok(lines: list[dict], synced: list[tuple[float, str]]) -> bool:
    if not lines:
        return False
    words = [w for ln in lines for w in ln["words"]]
    if len(words) < 20:
        print(f"  Ausrichtung verdächtig: nur {len(words)} Wörter")
        return False
    zero = sum(1 for w in words if w["e"] - w["s"] < 0.02)
    if zero > 0.35 * len(words):
        print(f"  Ausrichtung verdächtig: {zero}/{len(words)} Wörter ohne Dauer")
        return False
    if synced and len(synced) >= 4:
        lrc_starts = [t for t, txt in synced if txt]
        diffs = []
        for ln in lines:
            nearest = min(lrc_starts, key=lambda t: abs(t - ln["start"]))
            diffs.append(abs(nearest - ln["start"]))
        diffs.sort()
        median = diffs[len(diffs) // 2]
        print(f"  Abweichung zu LRC-Zeiten (Median): {median:.2f}s")
        if median > 2.5:
            return False
    return True


# --------------------------------------------------------------------------- Hauptfunktion

def build_lyrics(vocals_path: str, artist: str, title: str, duration: float) -> dict:
    print(f"Suche Lyrics für: {artist or '?'} – {title or '?'}")
    hit = find_lyrics(artist, title, duration)

    if hit and hit.get("instrumental"):
        print("  LRCLIB: Instrumental – keine Lyrics")
        return {"source": "instrumental", "language": None, "lines": []}

    synced = parse_lrc(hit["syncedLyrics"]) if hit and hit.get("syncedLyrics") else []
    plain = clean_lines(hit["plainLyrics"]) if hit and hit.get("plainLyrics") else []
    if hit:
        print(f"  Gefunden: {hit.get('artistName')} – {hit.get('trackName')} "
              f"({'synchron' if synced else 'nur Text'}, {len(plain)} Zeilen)")

    first_vocal = next((t for t, txt in synced if txt), 30.0) if synced else 30.0
    try:
        language = detect_language(vocals_path, at=first_vocal)
    except Exception as e:
        print(f"  Spracherkennung fehlgeschlagen: {e}")
        language = None

    if plain:
        try:
            model = _load_model()
            print("  Richte Lyrics Wort für Wort aus …")
            result = model.align(vocals_path, "\n".join(plain), language=language or "en",
                                 original_split=True)
            lines = _result_to_lines(result)
            if _alignment_ok(lines, synced):
                return {"source": "lrclib+align", "language": language,
                        "lrclib_id": hit.get("id"), "lines": _split_long(lines, 14)}
        except Exception as e:
            print(f"  Ausrichtung fehlgeschlagen: {e}")
        if synced:
            print("  Verwende Zeilen-Timing aus LRCLIB")
            return {"source": "lrclib-lines", "language": language,
                    "lrclib_id": hit.get("id"), "lines": lines_from_lrc(synced, duration)}

    print("  Keine passenden Lyrics – transkribiere den Gesang mit Whisper …")
    model = _load_model()
    result = model.transcribe(vocals_path, language=language, regroup=True, vad=False,
                              condition_on_previous_text=False)
    return {"source": "whisper", "language": language or result.language,
            "lines": _split_long(_result_to_lines(result))}
