# 🎤 Karaoke Studio

Familien-Karaoke: YouTube-Link oder MP3 rein, Karaoke-Song mit Lyrics raus.

- **Gesang entfernen** mit Demucs (KI)
- **Lyrics** von LRCLIB, Wort für Wort am Gesang ausgerichtet (Whisper). Gibt es keine Lyrics, wird der Gesang transkribiert.
- **Player** am Handy und PC mit Regler für die Gesangslautstärke (0–100 %)
- **Karaoke-Video** (MP4) – automatisch ohne Gesang, und per Knopfdruck mit jeder beliebigen Gesangslautstärke

Zwei Varianten:

- **PC-Version (Windows):** `Karaoke Studio starten.bat` doppelklicken. Richtet sich beim ersten Start selbst ein, nutzt eine NVIDIA-Grafikkarte automatisch und braucht weder GitHub noch Schlüssel. Am Handy im selben WLAN über die angezeigte Adresse öffnen.
- **Cloud-Version:** läuft kostenlos auf GitHub Actions, die App unter https://karaoke-studio-seven.vercel.app.

## PC-Version

1. Repo als ZIP herunterladen (Code → Download ZIP) und entpacken – oder mit Git klonen.
2. `Karaoke Studio starten.bat` doppelklicken. Beim ersten Mal werden Python, ffmpeg und die KI-Modelle in den Ordner `tools` geladen (10–20 Minuten).
3. Wenn Windows fragt, ob Python im Netzwerk erreichbar sein darf: **Zulassen** (privates Netzwerk) – dann klappt es auch am Handy.
4. Songs, Lyrics und Videos landen im Ordner `Daten`.

## So funktioniert es

```
App (Handy/PC) ──► GitHub Actions: process.yml
                     1. Audio laden (yt-dlp) oder MP3 aus inbox/
                     2. Gesang trennen (Demucs)
                     3. Lyrics suchen (LRCLIB) + Wort-Timing (stable-ts/Whisper)
                     4. songs/<id>/ speichern: instrumental.mp3, vocals.mp3, lyrics.json, meta.json
                     5. Karaoke-Video in den Branch „videos“ legen
App ◄── liest songs/ und spielt beide Spuren gemischt ab (Gesangsregler)
App ──► render.yml: Video mit gewählter Gesangslautstärke
```

| Ordner | Inhalt |
|---|---|
| `app/` | Web-App (HTML/CSS/JS, kein Build nötig) |
| `pipeline/` | Python-Skripte für Verarbeitung und Video |
| `.github/workflows/` | `process.yml` (Song verarbeiten), `render.yml` (Video exportieren) |
| `songs/` | Fertige Songs (wird automatisch befüllt) |
| `inbox/` | Hochgeladene MP3s, bis sie verarbeitet sind |

## Einrichtung (einmal pro Gerät)

1. Schlüssel erstellen: <https://github.com/settings/personal-access-tokens/new>
   - Repository access → **Only select repositories** → dieses Repo
   - Permissions → **Actions**: Read and write, **Contents**: Read and write
2. App öffnen, Schlüssel und Repo-Namen eingeben, fertig.
3. Am Handy: im Browser „Zum Startbildschirm hinzufügen“ – dann sieht sie aus wie eine echte App.

## Ohne App testen

Auf GitHub → **Actions** → **Song verarbeiten** → **Run workflow** → YouTube-Link einfügen.
Nach ca. 10–15 Minuten liegt das Video im Branch **videos** (Ordner mit der Song-ID).

## Gut zu wissen

- Ein Song braucht ca. 10–15 Actions-Minuten. Private Repos haben 2.000 Gratis-Minuten pro Monat.
- YouTube blockiert manchmal Downloads von Cloud-Servern. Dann einfach die MP3 hochladen.
- Die Songs sind nur für den privaten Gebrauch in der Familie gedacht.
- Bessere Lyrics-Erkennung (langsamer): in `process.yml` die Umgebungsvariable `WHISPER_MODEL: medium` setzen.
