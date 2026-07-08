![tveater logo](logo/tveater_180x180.png)

# tveater

tveater, pronounced "tee veeter", enables users of the HD HomeRun TV Tuners to view their video streams in a web browser, without installing any apps. tveater transcodes the video to the user's selected quality, enabling streaming over difficult links. 

Currently, only one tuner is used, and all concurrent sessions (users) will see the same video. I may add the use of the other tuners later. 

## Why a transcoder (not just an embedded URL)

The HDHomeRun serves **MPEG-2 video + AC3 audio in an MPEG-TS container**. No
browser plays that natively, so `server.py` runs one `ffmpeg` per active stream
to produce HLS. That same transcode is what gives you the quality/resolution
options — the real fix for watching over a weak network.

## Architecture

```
Browser (hls.js + dark UI)
   │  HLS  (H.264/AAC .m3u8 + .ts)
   ▼
server.py  (Python 3 stdlib, port 8089)
   ├─ /api/channels   parses lineup.m3u
   ├─ /api/stream     spawns ffmpeg (TS→HLS) for one channel/quality
   ├─ /api/stop       tears down ffmpeg (also via sendBeacon on page close)
   ├─ /api/status     stream state + actual output data rate (poll ~2s)
   ├─ /api/signal     tuner signal strength (poll 60s; hits the device)
   └─ /hls/*          serves the live HLS segments
   ▼
HDHomeRun @ <device-ip>  (:5004 streams, /status.json, /lineup.m3u)
```

Design choices (per requirements): **one stream at a time**, **standard HLS
(~6–10 s latency)**, **software x264** (`veryfast`) — a single transcode is
light enough to run on CPU, and x264 gives better quality than older on-board
NVENC encoders. ffmpeg is torn down when the browser closes (sendBeacon) or after
`IDLE_TIMEOUT` seconds with no segment requests (crash fallback), which frees
the tuner.

## Clone

```bash
git clone https://github.com/eliggett/tveater.git
cd tveater
```

## Requirements

- Python 3 (tested 3.12) — stdlib only, no `pip` packages
- `ffmpeg` with `libx264` + `aac`
- Network access to the HDHomeRun

## Configure

Config via env vars (see `config.py`): `TVEATER_PORT` (default 8089),
`TVEATER_DEVICE_IP` (your HDHomeRun's LAN IP), `TVEATER_X264_PRESET`.

## Run (development)

```bash
python3 server.py
# then open http://<server-ip>:8089/
```

## Deploy (systemd, standalone — no Apache)

Needs `sudo` (writes to `/etc/systemd/system`).

### 1. App service

```bash
cd /path/to/tveater
sudo cp deploy/tveater.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tveater.service
```

Verify:

```bash
systemctl status tveater.service --no-pager
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8089/     # expect 200
```

Then open http://localhost:8089/ (or `http://<server-ip>:8089/` from another machine).

### 2. Daily lineup refresh

```bash
sudo cp deploy/tveater-lineup.service deploy/tveater-lineup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tveater-lineup.timer
```

Verify (and optionally run a refresh now):

```bash
systemctl list-timers tveater-lineup.timer --no-pager
sudo systemctl start tveater-lineup.service                 # run it once now
journalctl -u tveater-lineup.service --no-pager -n 20       # expect "lineup updated"
```

### Everyday commands

```bash
sudo systemctl restart tveater      # after editing code
sudo systemctl stop tveater         # stop serving (frees the tuner)
journalctl -u tveater -f            # live logs
```

### Notes

- **Port / device IP** are set in `tveater.service`
  (`Environment=TVEATER_PORT=8089`, `TVEATER_DEVICE_IP=<device-ip>`). Edit the
  file, then `sudo systemctl daemon-reload && sudo systemctl restart tveater`.
- **LAN access:** the server binds `0.0.0.0`, so it is reachable at
  `http://<server-ip>:8089/`. If a firewall is active (e.g. `sudo ufw status`),
  open the port: `sudo ufw allow 8089/tcp`.
- Adjust `User=` and paths in the unit files to match your checkout location
  and account. Port 8089 is the default; pick any free port if it clashes with
  another service.
- See [here](https://info.hdhomerun.com/info/http_api) for the API reference. 

## Files

| Path | Purpose |
|---|---|
| `server.py` | HTTP server, routing, HLS serving |
| `transcode.py` | Single-session ffmpeg manager + idle watchdog |
| `hdhr.py` | lineup.m3u parsing + tuner status scraping |
| `config.py` | Ports, device IP, quality presets |
| `public/` | Dark UI (`index.html`, `style.css`, `app.js`, vendored `hls.js`) |
| `deploy/` | systemd units + daily lineup fetch script |

## Verified against the live device

Tuned 4.1 NBC4-LA @480p end to end:

- Transcode: source MPEG-2/AC3 → **H.264 854x480 + AAC**, valid 2s-segment HLS.
- Data rate readout: ~1807 kbps (matches the 480p target).
- Captions: `ATSC A53 Part 4 Closed Captions` SEI embedded (`-a53cc 1`), ready
  for hls.js to render.
- Signal scraper: `status.json` keys confirmed (strength/quality/symbol 100%).
- Lifecycle: SIGTERM and the idle watchdog both stop ffmpeg and free the tuner.

Known cosmetic note: the device reports `NetworkRate` 0 for :5004 HTTP pulls,
so the "in" rate on the signal line may show 0 Mbps — strength/quality are the
reliable fields.
