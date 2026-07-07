#!/usr/bin/env bash
# Download the current channel lineup from the HDHomeRun. Run daily via timer.
set -euo pipefail

DEVICE_IP="${TVEATER_DEVICE_IP:-10.0.0.224}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/lineup.m3u"
TMP="$(mktemp)"

if curl -fsS -m 30 "http://${DEVICE_IP}/lineup.m3u" -o "$TMP"; then
  # Only replace if we actually got a playlist, so a device blip can't wipe it.
  if head -1 "$TMP" | grep -q "#EXTM3U"; then
    mv "$TMP" "$DEST"
    echo "lineup updated: $DEST"
  else
    rm -f "$TMP"; echo "downloaded file was not an m3u playlist" >&2; exit 1
  fi
else
  rm -f "$TMP"; echo "failed to download lineup from ${DEVICE_IP}" >&2; exit 1
fi
