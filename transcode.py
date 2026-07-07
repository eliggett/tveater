"""Single-session ffmpeg transcode manager: HDHomeRun MPEG-TS -> HLS (H.264/AAC).

Only one stream runs at a time (per requirements). Starting a new stream, or
an idle/stop event, tears down the previous ffmpeg process so the tuner frees.
"""
import glob
import os
import shutil
import subprocess
import threading
import time

import config


class Session:
    """Holds the currently active transcode, guarded by a lock."""

    def __init__(self):
        self._lock = threading.RLock()
        self.proc = None
        self.channel = None      # channel dict
        self.quality = None
        self.last_access = 0.0
        self.started_at = 0.0
        self.epoch = 0           # bumps on every (re)start; lets clients detect switches

    # -- lifecycle ---------------------------------------------------------
    def start(self, channel, quality):
        """(Re)start the transcode for `channel` at `quality`. Returns when the
        playlist is ready, or raises TimeoutError if ffmpeg never produced it."""
        if quality not in config.QUALITIES:
            quality = config.DEFAULT_QUALITY
        with self._lock:
            self._stop_locked()
            os.makedirs(config.HLS_DIR, exist_ok=True)
            self._clear_dir()
            self.channel = channel
            self.quality = quality
            self.epoch += 1
            self.proc = subprocess.Popen(
                self._build_cmd(channel["id"], quality),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=config.HLS_DIR,
            )
            self.started_at = time.time()
            self.touch()
        self._wait_for_playlist()
        return {"playlist": "/hls/stream.m3u8", "quality": quality,
                "channel": channel}

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.channel = None
        self.quality = None

    def _clear_dir(self):
        for f in glob.glob(os.path.join(config.HLS_DIR, "*")):
            try:
                os.remove(f)
            except OSError:
                pass

    # -- helpers -----------------------------------------------------------
    def touch(self):
        self.last_access = time.time()

    def is_active(self):
        with self._lock:
            return self.proc is not None and self.proc.poll() is None

    def status(self):
        """Snapshot for /api/status."""
        with self._lock:
            if not self.is_active():
                return {"active": False, "epoch": self.epoch}
            return {
                "active": True,
                "epoch": self.epoch,
                # False during a (re)start until ffmpeg has written a playable
                # playlist; clients wait for this before attaching their player.
                "ready": self._playlist_ready(),
                "channel": self.channel,
                "quality": self.quality,
                "uptime": round(time.time() - self.started_at, 1),
                "out_kbps": self._measure_bitrate(),
            }

    def _playlist_ready(self):
        """True once the live playlist references at least one segment."""
        try:
            with open(os.path.join(config.HLS_DIR, "stream.m3u8")) as fh:
                return ".ts" in fh.read()
        except OSError:
            return False

    def _measure_bitrate(self):
        """Actual outgoing data rate (kbps) from recently written HLS segments.

        Uses completed segments only (skips the newest, which may be partial),
        dividing total bytes by their nominal duration.
        """
        segs = sorted(
            glob.glob(os.path.join(config.HLS_DIR, "seg_*.ts")),
            key=os.path.getmtime,
        )
        if len(segs) < 2:
            return None
        completed = segs[:-1][-4:]  # up to 4 most-recent completed segments
        total_bytes = sum(os.path.getsize(s) for s in completed)
        seconds = len(completed) * config.HLS_TIME
        if seconds <= 0:
            return None
        return round(total_bytes * 8 / seconds / 1000)

    def _wait_for_playlist(self, timeout=12):
        playlist = os.path.join(config.HLS_DIR, "stream.m3u8")
        deadline = time.time() + timeout
        while time.time() < deadline:
            # A ready live playlist references at least one segment.
            if os.path.exists(playlist):
                try:
                    with open(playlist) as fh:
                        if ".ts" in fh.read():
                            return
                except OSError:
                    pass
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError("ffmpeg exited before producing a stream")
            time.sleep(0.25)
        raise TimeoutError("stream did not start in time")

    def _build_cmd(self, channel_id, quality):
        q = config.QUALITIES[quality]
        # -a53cc 1 carries EIA-608/CEA-608 captions from the MPEG-2 source into
        # the H.264 bitstream as SEI so hls.js can render them.
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-fflags", "+genpts",
            "-i", config.stream_url(channel_id),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", config.X264_PRESET, "-a53cc", "1",
            "-b:v", q["vbr"], "-maxrate", q["maxrate"], "-bufsize", q["bufsize"],
            "-vf", f"scale={q['scale']}",
            "-g", str(config.HLS_TIME * 30), "-keyint_min", str(config.HLS_TIME * 30),
            "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", q["abr"], "-ac", "2",
            "-f", "hls",
            "-hls_time", str(config.HLS_TIME),
            "-hls_list_size", str(config.HLS_LIST_SIZE),
            "-hls_flags", "delete_segments+omit_endlist+independent_segments",
            "-hls_segment_filename", "seg_%05d.ts",
            "stream.m3u8",
        ]


# Module-level singleton + idle watchdog.
session = Session()


def _watchdog():
    while True:
        time.sleep(5)
        if session.is_active() and (time.time() - session.last_access) > config.IDLE_TIMEOUT:
            session.stop()


def start_watchdog():
    threading.Thread(target=_watchdog, daemon=True).start()
