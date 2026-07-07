"""Central configuration for tveater."""
import os

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


def stream_url(channel_id: str) -> str:
    """Build the HDHomeRun raw MPEG-TS URL for a virtual channel (e.g. '4.1')."""
    return f"http://{DEVICE_IP}:5004/auto/v{channel_id}"
