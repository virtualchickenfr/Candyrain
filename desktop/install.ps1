# Karaoke Studio – Einrichtung für Windows
# Installiert alles in den Ordner "tools" neben dieser App. Am System wird nichts verändert.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # macht Downloads deutlich schneller
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root  = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root "tools"
New-Item -ItemType Directory -Force $Tools | Out-Null

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Yellow }
function Download($url, $file) {
    for ($i = 1; $i -le 3; $i++) {
        try { Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing; return }
        catch { Write-Host "   Download fehlgeschlagen (Versuch $i): $($_.Exception.Message)"; Start-Sleep 3 }
    }
    throw "Konnte $url nicht laden. Bitte Internetverbindung prüfen."
}

Write-Host "Karaoke Studio wird eingerichtet. Das dauert beim ersten Mal 10–20 Minuten." -ForegroundColor Cyan
Write-Host "Alles landet im Ordner: $Tools"

# --- 1) Python (eigene, abgeschlossene Kopie) -------------------------------------------
$PyDir = Join-Path $Tools "python"
$Py    = Join-Path $PyDir "python.exe"
if (-not (Test-Path $Py)) {
    Step "Lade Python 3.11 …"
    $zip = Join-Path $env:TEMP "karaoke-python.zip"
    Download "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" $zip
    Expand-Archive -Force $zip $PyDir
    # "import site" aktivieren, damit pip-Pakete gefunden werden
    $pth = Get-ChildItem $PyDir -Filter "python*._pth" | Select-Object -First 1
    (Get-Content $pth.FullName) -replace "^#\s*import site", "import site" | Set-Content $pth.FullName -Encoding ASCII
    Step "Installiere pip …"
    $getpip = Join-Path $env:TEMP "karaoke-get-pip.py"
    Download "https://bootstrap.pypa.io/get-pip.py" $getpip
    & $Py $getpip --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "pip konnte nicht installiert werden." }
}
& $Py -m pip install --no-warn-script-location --disable-pip-version-check "setuptools<81" wheel
if ($LASTEXITCODE -ne 0) { throw "setuptools konnte nicht installiert werden." }

# --- 2) PyTorch – mit Grafikkarte (NVIDIA) oder Prozessor -----------------------------
$nvidia = $null
try { $nvidia = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1 } catch {}
if ($nvidia) {
    Step "NVIDIA-Grafikkarte gefunden: $($nvidia.Name) – installiere KI mit Grafikkarten-Beschleunigung (ca. 2,5 GB) …"
    $torchIndex = "https://download.pytorch.org/whl/cu121"
} else {
    Step "Keine NVIDIA-Grafikkarte gefunden – installiere KI für den Prozessor …"
    $torchIndex = "https://download.pytorch.org/whl/cpu"
}
& $Py -m pip install --no-warn-script-location --disable-pip-version-check torch==2.5.1 torchaudio==2.5.1 --index-url $torchIndex
if ($LASTEXITCODE -ne 0) { throw "PyTorch konnte nicht installiert werden." }

# --- 3) Karaoke-Pakete -----------------------------------------------------------------
Step "Installiere Gesangstrennung, Lyrics-Erkennung und YouTube-Download …"
& $Py -m pip install --no-warn-script-location --disable-pip-version-check --no-build-isolation -r (Join-Path $Root "pipeline\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Pakete konnten nicht installiert werden." }

# --- 4) ffmpeg (Audio/Video) -----------------------------------------------------------
$FfDir = Join-Path $Tools "ffmpeg"
if (-not (Test-Path (Join-Path $FfDir "ffmpeg.exe"))) {
    Step "Lade ffmpeg …"
    $zip = Join-Path $env:TEMP "karaoke-ffmpeg.zip"
    Download "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $zip
    $tmp = Join-Path $env:TEMP "karaoke-ffmpeg"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Force $zip $tmp
    New-Item -ItemType Directory -Force $FfDir | Out-Null
    Get-ChildItem $tmp -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $FfDir -Force
    Remove-Item -Recurse -Force $tmp
}

# --- 5) Deno (braucht yt-dlp für YouTube) ----------------------------------------------
$DenoDir = Join-Path $Tools "deno"
if (-not (Test-Path (Join-Path $DenoDir "deno.exe"))) {
    Step "Lade Deno (für YouTube-Downloads) …"
    $zip = Join-Path $env:TEMP "karaoke-deno.zip"
    Download "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" $zip
    Expand-Archive -Force $zip $DenoDir
}

# --- 6) KI-Modelle vorab laden ---------------------------------------------------------
$env:PATH = "$FfDir;$DenoDir;$PyDir;$PyDir\Scripts;$env:PATH"
Step "Lade die KI-Modelle (einmalig, ca. 1–2 GB) …"
& $Py -c "import torch; print('Grafikkarte nutzbar:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
& $Py -c "from demucs.pretrained import get_model; get_model('htdemucs'); print('Gesangstrennung bereit')"
& $Py -c "import sys; sys.path.insert(0, r'$Root\pipeline'); import lyrics; lyrics._load_model(); print('Lyrics-Erkennung bereit')"

Set-Content -Path (Join-Path $Tools "installed.txt") -Value "1"
Write-Host ""
Write-Host "Fertig eingerichtet!" -ForegroundColor Green
