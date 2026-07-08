"""Central configuration for tveater."""
import os
import re

# Where the HDHomeRun lives on the LAN.
DEVICE_IP = os.environ.get("TVEATER_DEVICE_IP", "10.0.0.224")

# Port for this web app. 80/8080/8086/8088 are already in use on the server.
PORT = int(os.environ.get("TVEATER_PORT", "8089"))

# Project paths.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
LINEUP_FILE = os.path.join(BASE_DIR, "lineup.m3u")

# Scratch dir for live HLS segments (one active stream at a time).
HLS_DIR = os.environ.get("TVEATER_HLS_DIR", "/tmp/tveater-hls")

# Kill the transcode if no HLS segment has been requested for this many
# seconds. Backstop for a crashed/killed browser that never sent a stop.
IDLE_TIMEOUT = 30

# HLS segmentation.
HLS_TIME = 2          # seconds per segment
HLS_LIST_SIZE = 6     # segments kept in the live playlist

# Transcode quality presets. libx264 software encode (see analysis: Kepler
# NVENC is lower quality and single-stream fits the Xeon easily).
# scale uses -2 to preserve aspect ratio and keep dimensions even.
QUALITIES = {
    "1080p": {"scale": "-2:1080", "vbr": "8000k", "maxrate": "8500k", "bufsize": "12000k", "abr": "160k"},
    "720p":  {"scale": "-2:720",  "vbr": "4000k", "maxrate": "4300k", "bufsize": "6000k",  "abr": "128k"},
    "480p":  {"scale": "-2:480",  "vbr": "1500k", "maxrate": "1700k", "bufsize": "3000k",  "abr": "96k"},
    "360p":  {"scale": "-2:360",  "vbr": "800k",  "maxrate": "900k",  "bufsize": "1500k",  "abr": "64k"},
}
DEFAULT_QUALITY = "720p"

# x264 preset: "veryfast" keeps a single 1080p realtime encode comfortable on
# the E5-2650 v2 while preserving good quality-per-bitrate.
X264_PRESET = os.environ.get("TVEATER_X264_PRESET", "veryfast")


def stream_url(channel: dict) -> str:
    """Build the HDHomeRun raw MPEG-TS URL for a channel dict.

    Uses the channel's explicit `tune` target when present (manual tuning);
    otherwise tunes the virtual channel by its id (e.g. '4.1' -> 'v4.1').
    """
    tune = channel.get("tune") or f"v{channel['id']}"
    return f"http://{DEVICE_IP}:5004/auto/{tune}"


def manual_channel(kind: str, value, sub="1"):
    """Build a channel dict for a manually tuned stream, or None if invalid.

    `kind` is "virtual", "physical", or "frequency"; `value` is the channel
    number or, for frequency, a value in MHz (one decimal allowed); `sub` is
    the sub-channel (defaults to 1). The resulting `tune` field is the
    HDHomeRun /auto/<tune> target:

        virtual   5      sub 1 -> v5.1
        physical  5      sub 1 -> ch5-1
        frequency 473.0  sub 3 -> ch473000000-3

    Inputs are validated to plain numbers so nothing untrusted reaches the
    device URL path.
    """
    value = str(value).strip()
    sub = str(sub).strip() or "1"
    if not sub.isdigit():
        return None
    if kind == "virtual":
        if not re.fullmatch(r"\d+", value):
            return None
        tune = f"v{value}.{sub}"
        number = f"{value}.{sub}"
        label = f"Virtual {value}.{sub}"
    elif kind == "physical":
        if not re.fullmatch(r"\d+", value):
            return None
        tune = f"ch{value}-{sub}"
        number = f"ch{value}-{sub}"
        label = f"Physical ch{value}, sub {sub}"
    elif kind == "frequency":
        if not re.fullmatch(r"\d+(\.\d)?", value):
            return None
        hz = int(round(float(value) * 1_000_000))
        tune = f"ch{hz}-{sub}"
        number = f"{value} MHz"
        label = f"{value} MHz, sub {sub}"
    else:
        return None
    return {
        "id": f"manual:{tune}",
        "number": number,
        "name": label,
        "title": label,
        "group": "Manual",
        "tune": tune,
    }
