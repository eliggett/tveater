"use strict";

const video = document.getElementById("video");
const placeholder = document.getElementById("placeholder");
const channelsEl = document.getElementById("channels");
const filterEl = document.getElementById("filter");
const qualityEl = document.getElementById("quality");
const captionsEl = document.getElementById("captions");
const nowPlayingEl = document.getElementById("now-playing");
const statusTextEl = document.getElementById("status-text");
const datarateEl = document.getElementById("datarate");
const signalEl = document.getElementById("signal");
const dot = document.getElementById("status-dot");
const appEl = document.getElementById("app");
const navToggle = document.getElementById("nav-toggle");
const mobileTitle = document.getElementById("mobilebar-title");

const isMobile = () => window.matchMedia("(max-width: 768px)").matches;
navToggle.addEventListener("click", () => appEl.classList.toggle("nav-collapsed"));

// The transcode is a single shared session server-side: every viewer watches
// the same /hls/stream.m3u8, and whoever changes channel/quality changes it for
// everyone. Clients stay in sync by polling /api/status and reconciling against
// the server's `epoch`, which bumps on every (re)start.
const PLAYLIST = "/hls/stream.m3u8";

let hls = null;
let current = null;        // active channel object (mirrors the shared session)
let allChannels = [];
let appliedEpoch = null;   // the session epoch this client is currently showing
let statusTimer = null;
let signalTimer = null;
let watchTimer = null;
let lastTime = 0;         // last observed video.currentTime (stall detection)
let stalledSince = 0;     // when a waiting/stall began (0 = not stalled)

// ---- Channel list ----------------------------------------------------------
async function loadChannels() {
  const res = await fetch("/api/channels");
  const data = await res.json();
  allChannels = data.channels || [];
  renderChannels(allChannels);
}

function renderChannels(list) {
  channelsEl.innerHTML = "";
  if (!list.length) {
    channelsEl.innerHTML = '<li class="loading">No channels</li>';
    return;
  }
  let lastGroup = null;
  for (const ch of list) {
    const group = ch.group || "Channels";
    if (group !== lastGroup) {
      const label = document.createElement("li");
      label.className = "group-label";
      label.textContent = group;
      channelsEl.appendChild(label);
      lastGroup = group;
    }
    const li = document.createElement("li");
    li.className = "channel";
    li.dataset.id = ch.id;
    if (current && current.id === ch.id) li.classList.add("active");
    li.innerHTML =
      `<span class="num">${ch.number}</span><span class="name">${escapeHtml(ch.name || ch.title)}</span>`;
    li.addEventListener("click", () => playChannel(ch));
    channelsEl.appendChild(li);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

filterEl.addEventListener("input", () => {
  const q = filterEl.value.trim().toLowerCase();
  if (!q) return renderChannels(allChannels);
  renderChannels(allChannels.filter((c) =>
    (c.name + " " + c.number + " " + c.title).toLowerCase().includes(q)));
});

// ---- Playback --------------------------------------------------------------
// Selecting a channel or changing quality only *requests* the switch; the actual
// UI + player update happens in reconcile() once the server confirms the new
// epoch — so the same code path runs whether this client or another one drove it.
async function playChannel(ch) {
  // On phones, collapse the list on selection so the video goes full-width.
  if (isMobile()) appEl.classList.add("nav-collapsed");
  applyChannelUI(ch);   // instant local feedback; reconcile() confirms authoritatively
  showBanner(`Switching to channel ${ch.number} ${ch.name || ch.title}… please wait a moment`);
  setDot("buffering");
  await requestStream(ch.id, qualityEl.value);
}

async function requestStream(channelId, quality) {
  let res;
  try {
    res = await fetch("/api/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel: channelId, quality }),
    });
  } catch (e) {
    return fail("network error");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    return fail(err.error || "stream failed");
  }
  // Reconcile immediately rather than waiting for the next poll tick.
  await pollStatus();
}

// Bring this client into sync with the shared session described by /api/status.
function reconcile(s) {
  if (!s.active) {
    if (appliedEpoch !== 0) {         // a stream we were showing has stopped
      appliedEpoch = 0;
      current = null;
      destroyHls();
      hideBanner();
      placeholder.style.display = "";
      nowPlayingEl.textContent = "—";
      mobileTitle.textContent = "tveater";
      setDot("idle");
      document.querySelectorAll("#channels .channel.active")
        .forEach((el) => el.classList.remove("active"));
    }
    return;
  }
  if (s.epoch === appliedEpoch) return;   // already showing this exact stream

  // A different stream than what we're showing is (re)starting or ready — maybe
  // driven by another viewer. Surface the banner as soon as we know the target…
  if (s.channel) {
    const sameChannel = current && current.id === s.channel.id;
    showBanner(sameChannel
      ? `Changing quality to ${s.quality}… please wait a moment`
      : `Switching to channel ${s.channel.number} ${s.channel.name || s.channel.title}… please wait a moment`);
    setDot("buffering");
  }
  // …but don't attach the player until ffmpeg has produced a playable playlist.
  // Attaching to the half-written playlist mid-restart leaves hls.js stuck, so
  // we hold here (banner up) and adopt on a later poll once ready flips true.
  if (!s.ready) return;

  appliedEpoch = s.epoch;
  current = s.channel;
  qualityEl.value = s.quality;
  applyChannelUI(current);
  attachHls(PLAYLIST);
}

function applyChannelUI(ch) {
  document.querySelectorAll("#channels .channel").forEach((el) =>
    el.classList.toggle("active", el.dataset.id === ch.id));
  nowPlayingEl.textContent = `${ch.number} ${ch.name || ch.title}`;
  mobileTitle.textContent = `${ch.number} ${ch.name || ch.title}`;
  placeholder.style.display = "none";
}

function showBanner(msg) {
  statusTextEl.textContent = msg;
  statusTextEl.style.display = "flex";
  placeholder.style.display = "none";
}

function hideBanner() {
  statusTextEl.style.display = "none";
}

function attachHls(playlist) {
  destroyHls();
  resetCaptions();
  if (window.Hls && Hls.isSupported()) {
    hls = new Hls({
      liveSyncDurationCount: 3,
      enableWebVTT: true,
      // CEA-608 captions render as native TextTracks on the <video>.
      renderTextTracksNatively: true,
    });
    hls.loadSource(playlist);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, tryPlay);
    hls.on(Hls.Events.ERROR, (_evt, d) => {
      if (d.fatal) {
        switch (d.type) {
          case Hls.ErrorTypes.NETWORK_ERROR: hls.startLoad(); break;
          case Hls.ErrorTypes.MEDIA_ERROR: hls.recoverMediaError(); break;
          default: fail("playback error");
        }
      }
    });
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = playlist; // Safari native HLS
    tryPlay();
  } else {
    fail("HLS unsupported in this browser");
  }
}

// Start playback without the user pressing play. Selecting a channel is a user
// gesture so audio autoplay is normally allowed; if a browser still blocks it,
// fall back to muted playback (video keeps rolling; unmute via the controls).
function tryPlay() {
  const p = video.play();
  if (p) p.catch(() => { video.muted = true; video.play().catch(() => {}); });
}

function destroyHls() {
  if (hls) { hls.destroy(); hls = null; }
  lastTime = 0;
  stalledSince = 0;
}

// Restart transcode when quality changes (server re-spawns ffmpeg for everyone).
qualityEl.addEventListener("change", () => {
  if (!current) return;
  showBanner(`Changing quality to ${qualityEl.value}… please wait a moment`);
  setDot("buffering");
  requestStream(current.id, qualityEl.value);
});

video.addEventListener("playing", () => { setDot("live"); stalledSince = 0; hideBanner(); });
video.addEventListener("waiting", () => { setDot("buffering"); if (!stalledSince) stalledSince = Date.now(); });
video.addEventListener("stalled", () => { if (!stalledSince) stalledSince = Date.now(); });

// Watchdog: the browser sometimes leaves its buffering spinner up on a channel
// switch even though playback resumed (the "playing" event got missed). Every
// second, if the video is clearly advancing while a stall is still flagged, the
// spinner is stale — nudge the element (a no-op seek to the same spot) to clear
// it without a visible jump.
function watchPlayback() {
  if (!current || video.paused) { lastTime = video.currentTime; return; }
  const advancing = video.currentTime > lastTime + 0.05;
  lastTime = video.currentTime;
  if (!advancing) return;                       // genuinely buffering — leave it
  if (dot.className !== "live") setDot("live");
  if (stalledSince && Date.now() - stalledSince > 1200) {
    const t = video.currentTime;
    try { video.currentTime = t; } catch (_) { /* ignore */ }
    stalledSince = 0;
  }
}

function fail(msg) {
  setDot("error");
  hideBanner();
  datarateEl.textContent = "Data Rate Out: —";
  nowPlayingEl.textContent = `${current ? current.number + " " : ""}⚠ ${msg}`;
}

function setDot(state) {
  dot.className = state;
}

// ---- Captions (CEA-608) ----------------------------------------------------
function resetCaptions() {
  captionsEl.innerHTML = '<option value="-1">Off</option>';
}

// Caption tracks appear as the decoder encounters them; rebuild on add.
video.textTracks.addEventListener("addtrack", rebuildCaptions);

function rebuildCaptions() {
  const tracks = [...video.textTracks].filter(
    (t) => t.kind === "captions" || t.kind === "subtitles");
  const chosen = captionsEl.value;
  resetCaptions();
  tracks.forEach((t, i) => {
    t.mode = "disabled";
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = t.label || t.language || `CC${i + 1}`;
    captionsEl.appendChild(opt);
  });
  captionsEl.value = chosen <= tracks.length - 1 ? chosen : "-1";
  applyCaptionSelection();
}

captionsEl.addEventListener("change", applyCaptionSelection);

function applyCaptionSelection() {
  const idx = parseInt(captionsEl.value, 10);
  const tracks = [...video.textTracks].filter(
    (t) => t.kind === "captions" || t.kind === "subtitles");
  tracks.forEach((t, i) => { t.mode = i === idx ? "showing" : "disabled"; });
}

// ---- Readouts: data rate (2s) + signal (60s) -------------------------------
// Polling runs for the whole page lifetime (not just after this client starts a
// stream) so viewers who never touched the remote still track the shared state.
function startPolling() {
  pollStatus();
  pollSignal();
  statusTimer = setInterval(pollStatus, 2000);
  signalTimer = setInterval(pollSignal, 60000);
  watchTimer = setInterval(watchPlayback, 1000);
}

async function pollStatus() {
  let s;
  try {
    s = await (await fetch("/api/status")).json();
  } catch (_) { return; }   // transient
  reconcile(s);
  if (!s.active) { datarateEl.textContent = "Data Rate Out: —"; return; }
  datarateEl.textContent = s.out_kbps != null
    ? `Data Rate Out: ${(s.out_kbps / 1000).toFixed(1)} Mbps`
    : "Data Rate Out: measuring…";
}

async function pollSignal() {
  try {
    const { signal } = await (await fetch("/api/signal")).json();
    if (!signal) { signalEl.textContent = "Signal: —"; return; }
    const bits = [];
    if (signal.strength != null) bits.push(`${signal.strength}%`);
    if (signal.freq_mhz != null) bits.push(`${signal.freq_mhz.toFixed(3)} MHz`);
    if (signal.quality != null) bits.push(`Q ${signal.quality}%`);
    signalEl.textContent = "Signal: " + (bits.join(" · ") || "—");
  } catch (_) { signalEl.textContent = "Signal: —"; }
}

// ---- Desktop keyboard: "F" toggles fullscreen -------------------------------
function toggleFullscreen() {
  const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
  if (!fsEl) {
    const req = video.requestFullscreen || video.webkitRequestFullscreen
      || video.webkitEnterFullscreen;
    if (req) req.call(video);
  } else {
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if (exit) exit.call(document);
  }
}

document.addEventListener("keydown", (e) => {
  if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey && !e.altKey) {
    const tag = (document.activeElement?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return; // don't hijack typing
    e.preventDefault();
    toggleFullscreen();
  }
});

// The stream is shared, so a viewer leaving must NOT stop it for everyone else.
// When the last viewer's player stops fetching segments, the server's idle
// watchdog tears the transcode down on its own (freeing the tuner).

loadChannels();
startPolling();
