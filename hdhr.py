"""HDHomeRun helpers: parse the channel lineup and read tuner status."""
import json
import re
import urllib.request

import config

_EXTINF_RE = re.compile(r'#EXTINF:-1\s+(?P<attrs>.*),(?P<title>.*)')
_ATTR_RE = re.compile(r'(\S+?)="(.*?)"')


def parse_lineup(path=None):
    """Parse lineup.m3u into a list of channel dicts.

    Returns items like:
        {"id": "4.1", "number": "4.1", "name": "NBC4-LA",
         "title": "4.1 NBC4-LA", "group": "Favorites"}
    """
    path = path or config.LINEUP_FILE
    channels = []
    pending = None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                m = _EXTINF_RE.match(line)
                if not m:
                    pending = None
                    continue
                attrs = dict(_ATTR_RE.findall(m.group("attrs")))
                pending = {
                    "id": attrs.get("channel-id") or attrs.get("channel-number", ""),
                    "number": attrs.get("channel-number", ""),
                    "name": attrs.get("tvg-name", "").strip(),
                    "title": m.group("title").strip(),
                    "group": attrs.get("group-title", "Channels"),
                }
            elif not line.startswith("#") and pending is not None:
                pending["url"] = line
                channels.append(pending)
                pending = None
    return channels


def grouped_lineup(path=None):
    """Return channels ordered for the UI: Favorites first, then playlist order.

    Channels in the "Favorites" group-title are moved to the front; all others
    keep their original lineup order. The sort is stable, so relative order
    within each group matches the playlist.
    """
    channels = parse_lineup(path)
    channels.sort(key=lambda c: 0 if c["group"] == "Favorites" else 1)
    return channels


def _fetch(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def tuner_status():
    """Return the device's per-tuner status list from /status.json.

    Modern HDHomeRun firmware exposes http://<ip>/status.json as a JSON array,
    one object per active tuner, with keys like VctNumber, VctName,
    SignalStrengthPercent, SignalQualityPercent, SymbolQualityPercent,
    NetworkRate, TargetIP. Returns [] on any error so callers degrade cleanly.

    Verified against the live device (firmware returns exactly these keys).
    NetworkRate reads 0 for streams pulled over the :5004 HTTP interface (as
    this app does), so signal_for() may report rate_mbps 0.0 — strength/quality
    are the reliable fields.
    """
    try:
        return json.loads(_fetch(f"http://{config.DEVICE_IP}/status.json"))
    except Exception:
        return []


def signal_for(channel_number):
    """Return signal info for the tuner currently on `channel_number`, or None.

    Output shape (percentages 0-100, freq in MHz, rate in Mbps):
        {"strength": 100, "quality": 100, "symbol": 100, "freq_mhz": 605.0,
         "rate_mbps": 10.4, "tuner": "tuner0", "vct": "4.1", "name": "NBC4-LA"}
    """
    for t in tuner_status():
        vct = str(t.get("VctNumber", ""))
        if vct and vct == str(channel_number):
            rate = t.get("NetworkRate")
            freq = t.get("Frequency")
            return {
                "strength": t.get("SignalStrengthPercent"),
                "quality": t.get("SignalQualityPercent"),
                "symbol": t.get("SymbolQualityPercent"),
                "freq_mhz": round(freq / 1_000_000, 3) if isinstance(freq, (int, float)) else None,
                "rate_mbps": round(rate / 1_000_000, 3) if isinstance(rate, (int, float)) else None,
                "tuner": t.get("Resource"),
                "vct": vct,
                "name": t.get("VctName"),
            }
    return None
