#!/usr/bin/env python3
"""tveater: web front-end for the HD HomeRun TV tuner.

Serves the UI + API and manages a single ffmpeg transcode (MPEG-2/AC3 TS ->
H.264/AAC HLS) so browsers can play the live streams. Stdlib only.
"""
import json
import os
import posixpath
import signal as _signal
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import hdhr
from transcode import session, start_watchdog

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "tveater/1.0"

    # -- helpers -----------------------------------------------------------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _serve_file(self, abs_path, no_cache=False):
        if not os.path.isfile(abs_path):
            self.send_error(404)
            return
        ext = os.path.splitext(abs_path)[1].lower()
        ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
        try:
            with open(abs_path, "rb") as fh:
                data = fh.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if no_cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _safe_join(self, root, rel):
        """Join and confine to `root`; returns None on traversal attempts."""
        rel = urllib.parse.unquote(rel).lstrip("/")
        abs_path = os.path.normpath(os.path.join(root, rel))
        if abs_path != root and not abs_path.startswith(root + os.sep):
            return None
        return abs_path

    # -- routing -----------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/":
            self._serve_file(os.path.join(config.PUBLIC_DIR, "index.html"))
            return
        if path == "/api/channels":
            self._send_json({"channels": hdhr.grouped_lineup()})
            return
        if path == "/api/status":
            # Lightweight: safe to poll every couple seconds (no device call).
            self._send_json(session.status())
            return
        if path == "/api/signal":
            # Hits the device's status.json; client polls this only every 60s.
            st = session.status()
            if st.get("active") and st.get("channel"):
                self._send_json({"signal": hdhr.signal_for(st["channel"]["number"])})
            else:
                self._send_json({"signal": None})
            return
        if path.startswith("/hls/"):
            self._serve_hls(path)
            return

        # Static assets from public/.
        abs_path = self._safe_join(config.PUBLIC_DIR, path)
        if abs_path is None:
            self.send_error(403)
            return
        self._serve_file(abs_path)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/stream":
            body = self._read_json_body()
            channel = self._resolve_channel(body.get("channel"))
            if channel is None:
                self._send_json({"error": "unknown channel"}, 400)
                return
            quality = body.get("quality", config.DEFAULT_QUALITY)
            try:
                result = session.start(channel, quality)
            except Exception as exc:  # ffmpeg/tuner failure -> surface to UI
                self._send_json({"error": str(exc)}, 502)
                return
            self._send_json(result)
            return

        if path == "/api/stop":
            session.stop()
            self._send_json({"stopped": True})
            return

        self.send_error(404)

    # -- HLS + channel resolution -----------------------------------------
    def _serve_hls(self, path):
        rel = posixpath.basename(path)  # only bare filenames live in HLS_DIR
        abs_path = self._safe_join(config.HLS_DIR, rel)
        if abs_path is None:
            self.send_error(403)
            return
        session.touch()  # any HLS request keeps the stream alive
        self._serve_file(abs_path, no_cache=True)

    def _resolve_channel(self, ref):
        """Accept a channel id/number string, a channel-like dict, or a manual
        tune spec ({"manual": true, "kind", "value", "sub"})."""
        if isinstance(ref, dict):
            if ref.get("manual"):
                return config.manual_channel(
                    ref.get("kind"), ref.get("value", ""), ref.get("sub", "1"))
            if ref.get("id"):
                return ref
        if isinstance(ref, str):
            for c in hdhr.parse_lineup():
                if c["id"] == ref or c["number"] == ref:
                    return c
        return None

    def log_message(self, fmt, *args):
        # Quiet the default per-request stderr spam (segments are frequent).
        pass


def main():
    start_watchdog()
    httpd = ThreadingHTTPServer(("0.0.0.0", config.PORT), Handler)

    def shutdown(*_):
        # shutdown() must run off the serve_forever() thread or it deadlocks,
        # so hand it to a helper thread; serve_forever() then returns cleanly
        # and the finally-block stops ffmpeg (frees the tuner).
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    _signal.signal(_signal.SIGTERM, shutdown)
    _signal.signal(_signal.SIGINT, shutdown)
    print(f"tveater listening on http://0.0.0.0:{config.PORT}  (device {config.DEVICE_IP})")
    try:
        httpd.serve_forever()
    finally:
        session.stop()


if __name__ == "__main__":
    main()
