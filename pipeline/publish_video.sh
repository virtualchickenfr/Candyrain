#!/usr/bin/env bash
# Legt ein fertiges Video in den Branch "videos" (dort holt die App es ab).
# Aufruf: bash pipeline/publish_video.sh <song_id> <datei.mp4>
set -euo pipefail
SONG_ID="$1"
FILE="$(realpath "$2")"
NAME="$(basename "$FILE")"

HDR="$(git config --get http.https://github.com/.extraheader)"
W="${RUNNER_TEMP:-/tmp}/videos-branch"
rm -rf "$W" && mkdir -p "$W" && cd "$W"
git init -q
git config http.https://github.com/.extraheader "$HDR"
git config user.name "karaoke-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git remote add origin "https://github.com/${GITHUB_REPOSITORY}.git"

for i in 1 2 3 4 5; do
  if git fetch -q --depth 1 origin videos 2>/dev/null; then
    git checkout -q -f -B videos FETCH_HEAD
  else
    git checkout -q -f --orphan videos
    git rm -rq --cached . 2>/dev/null || true
  fi
  mkdir -p "$SONG_ID"
  cp "$FILE" "$SONG_ID/$NAME"
  git add "$SONG_ID/$NAME"
  git commit -q -m "🎬 Video: $NAME" || true
  if git push -q origin HEAD:videos; then
    echo "Video veröffentlicht: $SONG_ID/$NAME"
    exit 0
  fi
  echo "Push fehlgeschlagen (Versuch $i), neuer Versuch …"
  sleep $((i * 4))
done
exit 1
