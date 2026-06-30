"""HTML page generators for the browser-based exhibit UI.

Each page is a self-contained HTML document (inline CSS + JS) so the server
needs no separate static-file routes. The white+blue theme mirrors
``displays/theme.py`` so the look matches the Qt version.

Pages:
  - top page         : video stage + topic/speaker/gaze presentation
  - mini page (×role): subtitle + audio-reactive orb + gaze guidance
  - all page         : the four pages tiled in iframes (single-machine test)
  - index            : a simple link list
"""

from __future__ import annotations

import json

from ethics_podcast.displays import theme

ROLES = ("student", "professor", "moderator")


def _client_config() -> dict:
    """Theme values the browser JS needs, derived from displays/theme.py."""
    return {
        "roles": list(ROLES),
        "names": theme.ROLE_NAMES,
        "icons": {r: f"/icon/{r}" for r in ROLES},
        "accent": theme.ROLE_ACCENT,
        "accentRgb": {r: list(theme.role_accent_rgb(r)) for r in ROLES},
        "position": theme.ROLE_POSITION,
        "glowRgb": list(theme.RGB_ACCENT_GLOW),
        "font": theme.FONT_FAMILY,
    }


def _theme_vars() -> str:
    """A small :root{} block of CSS variables built from the theme."""
    return (
        ":root{"
        f"--white:{theme.WHITE};--snow:{theme.SNOW};--mist:{theme.MIST};"
        f"--ink:{theme.INK};--ink-soft:{theme.INK_SOFT};"
        f"--accent:{theme.ACCENT};--accent-deep:{theme.ACCENT_DEEP};"
        f"--accent-glow:{theme.ACCENT_GLOW};--line:{theme.LINE};"
        f"--font:{theme.FONT_FAMILY},system-ui,Segoe UI,Roboto,sans-serif;"
        "}"
    )


# ── Shared gaze-flow canvas animation (ported from displays/gaze_flow.py) ────

_GAZE_JS = r"""
function createGaze(canvas, glowRgb, options = {}) {
  const ctx = canvas.getContext('2d');
  let dir = [1, 0];
  let accent = [31, 139, 255];
  let phase = 0;
  let raf = null;
  let heroAlpha = 0;
  const hero = !!options.hero;

  function resize() {
    const r = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(r.width));
    canvas.height = Math.max(1, Math.floor(r.height));
  }
  window.addEventListener('resize', resize);

  function setDirection(dx, dy) {
    const len = Math.hypot(dx, dy);
    dir = len < 1e-6 ? [1, 0] : [dx / len, dy / len];
  }
  function setAccent(rgb) { accent = rgb; }

  function draw() {
    resize();
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (w <= 0 || h <= 0) { raf = requestAnimationFrame(draw); return; }

    const [dx, dy] = dir;
    const [r, g, b] = accent;
    const cx = w / 2, cy = h / 2;
    let axis = Math.abs(dx) * w + Math.abs(dy) * h;
    axis = Math.max(axis, 1);
    const px = -dy, py = dx;

    const count = 5;
    const arm = Math.min(w, h) * 0.16;
    for (let i = 0; i < count; i++) {
      const t = (phase * 0.9 + i / count) % 1.0;
      const fade = Math.sin(t * Math.PI);
      const alpha = (40 + 150 * fade) / 255;
      const offset = (t - 0.5) * axis;
      const ox = cx + dx * offset, oy = cy + dy * offset;

      ctx.strokeStyle = `rgba(${r},${g},${b},${alpha})`;
      ctx.lineWidth = 6;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';

      const tipx = ox + dx * arm * 0.5, tipy = oy + dy * arm * 0.5;
      const bx = ox - dx * arm * 0.5, by = oy - dy * arm * 0.5;
      const a1x = bx + px * arm * 0.6, a1y = by + py * arm * 0.6;
      const a2x = bx - px * arm * 0.6, a2y = by - py * arm * 0.6;
      ctx.beginPath();
      ctx.moveTo(a1x, a1y); ctx.lineTo(tipx, tipy);
      ctx.moveTo(a2x, a2y); ctx.lineTo(tipx, tipy);
      ctx.stroke();
    }

    const [gr, gg, gb] = glowRgb;
    for (let i = 0; i < 18; i++) {
      const t = (phase * 1.4 + i * 0.137) % 1.0;
      const fade = Math.sin(t * Math.PI);
      const alpha = (30 + 140 * fade) / 255;
      const offset = (t - 0.5) * axis;
      const jitter = Math.sin(phase * 3.0 + i) * Math.min(w, h) * 0.08;
      const ox = cx + dx * offset + px * jitter;
      const oy = cy + dy * offset + py * jitter;
      const size = 3 + 5 * fade;
      ctx.fillStyle = `rgba(${gr},${gg},${gb},${alpha})`;
      ctx.beginPath();
      ctx.arc(ox, oy, size / 2, 0, Math.PI * 2);
      ctx.fill();
    }

    if (hero) {
      heroAlpha += (1 - heroAlpha) * 0.18;
      const heroArm = Math.min(w, h) * 0.46;
      const heroBase = heroArm * 0.55;
      const glowAlpha = 0.28 * heroAlpha;
      const lineAlpha = 0.82 * heroAlpha;
      const tipx = cx + dx * heroArm * 0.38, tipy = cy + dy * heroArm * 0.38;
      const bx = cx - dx * heroArm * 0.38, by = cy - dy * heroArm * 0.38;
      const a1x = bx + px * heroBase, a1y = by + py * heroBase;
      const a2x = bx - px * heroBase, a2y = by - py * heroBase;

      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(a1x, a1y); ctx.lineTo(tipx, tipy);
      ctx.moveTo(a2x, a2y); ctx.lineTo(tipx, tipy);
      ctx.strokeStyle = `rgba(${r},${g},${b},${glowAlpha})`;
      ctx.lineWidth = Math.min(w, h) * 0.12;
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(a1x, a1y); ctx.lineTo(tipx, tipy);
      ctx.moveTo(a2x, a2y); ctx.lineTo(tipx, tipy);
      ctx.strokeStyle = `rgba(${r},${g},${b},${lineAlpha})`;
      ctx.lineWidth = Math.min(w, h) * 0.045;
      ctx.stroke();

      const halo = ctx.createRadialGradient(cx, cy, 0, cx, cy, heroArm * 0.92);
      halo.addColorStop(0, `rgba(${r},${g},${b},${0.16 * heroAlpha})`);
      halo.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(cx, cy, heroArm * 0.92, 0, Math.PI * 2);
      ctx.fill();
    }

    phase += 0.02;
    raf = requestAnimationFrame(draw);
  }

  return {
    start() { canvas.style.display = 'block'; heroAlpha = 0; if (!raf) draw(); },
    stop() {
      canvas.style.display = 'none';
      heroAlpha = 0;
      if (raf) { cancelAnimationFrame(raf); raf = null; }
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    },
    setDirection, setAccent,
  };
}
"""


# ── Small WebSocket helper with auto-reconnect ───────────────────────────────

_WS_JS = r"""
// Shared across every connectWS() call on a page (the /all page opens four).
// First boot id seen is just recorded; a CHANGED id means the program was
// restarted, so the display reloads itself to pick up a clean slate.
let __bootId = null;
function connectWS(role, onMessage) {
  let ws = null;
  function open() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/${role}`);
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg && msg.bootId) {
        if (__bootId === null) { __bootId = msg.bootId; }
        else if (msg.bootId !== __bootId) { location.reload(); return; }
      }
      try { onMessage(msg, ws); } catch (e) {}
    };
    ws.onclose = () => setTimeout(open, 800);
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }
  open();
  return { send: (obj) => { try { ws && ws.readyState === 1 && ws.send(JSON.stringify(obj)); } catch (e) {} } };
}
"""


# ── Top page ─────────────────────────────────────────────────────────────────

def page_top() -> str:
    cfg = json.dumps(_client_config())
    html = _TOP_HTML
    html = html.replace("/*THEME_VARS*/", _theme_vars())
    html = html.replace("/*GAZE_JS*/", _GAZE_JS)
    html = html.replace("/*WS_JS*/", _WS_JS)
    html = html.replace("/*CONFIG*/", cfg)
    return html


_TOP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ethics Podcast — Top Display</title>
<style>
/*THEME_VARS*/
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: 100%; height: 100%; overflow: hidden; background: #000;
  font-family: var(--font); cursor: auto; }
.layer { position: fixed; inset: 0; opacity: 0; visibility: hidden;
  pointer-events: none; transform: scale(1.01);
  transition: opacity .7s ease, visibility 0s linear .7s, transform .7s ease; }
.layer.on { opacity: 1; visibility: visible; pointer-events: auto; transform: scale(1);
  transition: opacity .7s ease, visibility 0s linear 0s, transform .7s ease; }
#video-layer { background: #000; z-index: 1; }
#vid { width: 100%; height: 100%; object-fit: contain; background: #000;
  opacity: 0; transform: scale(1.03); transition: opacity .75s ease, transform .75s ease; }
#video-layer.on #vid { opacity: 1; transform: scale(1); }
#topic-layer {
  background: linear-gradient(180deg, var(--white), var(--snow));
  padding: 6vh 6vw; flex-direction: column; align-items: center; z-index: 2;
  display: flex; }
#topic-layer::before {
  content: ""; position: absolute; inset: 2.4vh 2.4vw;
  border: 2px solid rgba(31,139,255,0.16); border-radius: 28px; pointer-events: none; }
.app-title { color: var(--accent); font-size: 2.0vw; font-weight: 900;
  letter-spacing: 0.5vw; text-align: center; }
.title { color: var(--ink); font-size: 3.8vw; font-weight: 900;
  text-align: center; margin-top: 3vh; }
.topic { color: var(--accent-deep); font-size: 3.0vw; font-weight: 700;
  text-align: center; line-height: 1.35; margin-top: 4vh; flex: 1;
  display: flex; align-items: center; justify-content: center; }
.markers { display: flex; gap: 0; width: 100%; justify-content: space-evenly;
  align-items: flex-end; margin-top: 2vh; }
.marker { display: flex; flex-direction: column; align-items: center; gap: 2vh;
  transition: all .3s ease; }
.marker .name { font-size: 1.3vw; font-weight: 600; color: var(--ink-soft);
  text-align: center; white-space: nowrap; transition: all .3s ease; }
.robot-icon { width: 22vw; height: 22vw; min-width: 100px; flex: 0 0 auto;
  display: grid; place-items: center; border-radius: 50%; padding: 0.35vw;
  background: rgba(255,255,255,0.78); border: 2px solid rgba(150,170,195,0.42);
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.7);
  filter: drop-shadow(0 0 0 rgba(31,139,255,0));
  transform: translateY(0) scale(0.86); opacity: 0.74;
  transition: transform .35s ease, border-color .35s ease, opacity .35s ease,
    filter .35s ease, box-shadow .35s ease; }
.robot-icon img { width: 100%; aspect-ratio: 1 / 1; height: auto; object-fit: cover; border-radius: 50%; display: block; }
.marker.active .robot-icon { transform: scale(1.06);
  opacity: 1; border-color: var(--role-color);
  box-shadow: inset 0 0 0 2px rgba(255,255,255,0.85), 0 0 22px var(--role-glow);
  filter: drop-shadow(0 0 18px var(--role-glow)); }
.marker.active .name { font-weight: 900; color: var(--ink); }
#gaze { position: absolute; inset: 0; display: none; pointer-events: none; }
#fade-curtain { position: fixed; inset: 0; z-index: 45; opacity: 0;
  visibility: hidden; pointer-events: none; background: #000;
  transition: opacity 1s ease, visibility 0s linear 1s; }
#fade-curtain.on { opacity: 1; visibility: visible;
  transition: opacity 1s ease, visibility 0s linear 0s; }
#startov { position: fixed; inset: 0; z-index: 50; display: none;
  align-items: center; justify-content: center; cursor: pointer;
  background: rgba(0,0,0,0.72); color: #fff; font-size: 3vw; font-weight: 800;
  text-align: center; padding: 4vw; }
</style>
</head>
<body>
  <div id="video-layer" class="layer"><video id="vid" autoplay playsinline webkit-playsinline preload="auto"></video></div>
  <audio id="speech-audio" preload="auto"></audio>
  <audio id="bgm" preload="auto" loop></audio>
  <div id="fade-curtain"></div>
  <div id="startov">▶ クリックで動画を開始<br>Click to start</div>
  <div id="topic-layer" class="layer">
    <div class="app-title">AI ETHICS · LIVE</div>
    <div class="title" id="title"></div>
    <div class="topic" id="topic"></div>
    <div class="markers" id="markers"></div>
    <canvas id="gaze"></canvas>
  </div>

<script>/*WS_JS*/</script>
<script>/*GAZE_JS*/</script>
<script>
const CFG = /*CONFIG*/;
const videoLayer = document.getElementById('video-layer');
const topicLayer = document.getElementById('topic-layer');
const vid = document.getElementById('vid');
const speechAudio = document.getElementById('speech-audio');
const bgmAudio = document.getElementById('bgm');
let curBgmSrc = '';
// BGM fades in when a discussion starts and out when it ends, so it never
// snaps on/off. The steady-state volume comes from the server (bgmVolume).
let bgmTargetVolume = 0.18;
let bgmFadeTimer = null;
const BGM_FADE_IN_MS = 2000;
const BGM_FADE_OUT_MS = 1500;
const clamp01 = (v) => Math.max(0, Math.min(1, v));
function clearBgmFade() {
  if (bgmFadeTimer !== null) { clearInterval(bgmFadeTimer); bgmFadeTimer = null; }
}
// Ramp the BGM element volume to `to` over `ms`, then run onDone (if any). The
// timer runs independently of play state, so a fade still completes even while
// autoplay is blocked.
function fadeBgm(to, ms, onDone) {
  clearBgmFade();
  to = clamp01(to);
  const from = clamp01(bgmAudio.volume);
  if (ms <= 0 || from === to) { bgmAudio.volume = to; if (onDone) onDone(); return; }
  const startedAt = Date.now();
  bgmFadeTimer = setInterval(() => {
    const t = Math.min(1, (Date.now() - startedAt) / ms);
    bgmAudio.volume = clamp01(from + (to - from) * t);
    if (t >= 1) { clearBgmFade(); if (onDone) onDone(); }
  }, 30);
}
// All exhibit audio lives in the browser, so muting just gates these elements.
function applyMute(s) {
  if (typeof s.muted !== 'boolean') return;
  speechAudio.muted = s.muted;
  bgmAudio.muted = s.muted;
  vid.muted = s.muted;
}
// BGM is mono/centered: the <audio> element plays straight to the output with
// equal L/R, so it does not follow the per-speaker speech panning.
function tryPlayBgm() {
  if (!bgmAudio.src) return;
  const pr = bgmAudio.play();
  if (pr && pr.catch) pr.catch((e) => {
    console.warn('[top] bgm blocked — click display once:', e && e.name);
  });
}
function applyBgm(s) {
  if (typeof s.bgmVolume === 'number') bgmTargetVolume = clamp01(s.bgmVolume);
  if (s.bgmSrc && s.bgmSrc !== curBgmSrc) {
    // New track: start silent and fade in.
    curBgmSrc = s.bgmSrc;
    bgmAudio.src = s.bgmSrc;
    bgmAudio.loop = true;
    bgmAudio.volume = 0;
    bgmAudio.load();
    tryPlayBgm();
    fadeBgm(bgmTargetVolume, BGM_FADE_IN_MS);
  } else if (!s.bgmSrc && curBgmSrc) {
    // Track removed: fade out, then release the element.
    curBgmSrc = '';
    fadeBgm(0, BGM_FADE_OUT_MS, () => {
      try { bgmAudio.pause(); bgmAudio.removeAttribute('src'); bgmAudio.load(); } catch (e) {}
    });
  } else if (s.bgmSrc && bgmFadeTimer === null) {
    // Same track, a volume update arrived outside a fade — apply it directly.
    bgmAudio.volume = bgmTargetVolume;
  }
}
const fadeCurtain = document.getElementById('fade-curtain');
const gaze = createGaze(document.getElementById('gaze'), CFG.glowRgb);
let speechAudioCtx = null;
let speechPanner = null;
let speechSource = null;

// Build robot markers left -> center -> right by physical position.
const ordered = [...CFG.roles].sort((a, b) => CFG.position[a] - CFG.position[b]);
const markersEl = document.getElementById('markers');
const markerByRole = {};
function robotIcon(role) {
  return `
    <div class="robot-icon" style="--role-color:${CFG.accent[role]};--role-glow:rgba(${(CFG.accentRgb[role] || CFG.glowRgb).join(',')},0.34)">
      <img src="${CFG.icons[role]}" alt="${CFG.names[role] || role}">
    </div>`;
}
for (const role of ordered) {
  const m = document.createElement('div');
  m.className = 'marker';
  m.innerHTML = `<div class="name">${CFG.names[role] || role}</div>` + robotIcon(role);
  markersEl.appendChild(m);
  markerByRole[role] = m;
}

let curSrc = '', curEpoch = -1, curSpeechEpoch = -1;
const startov = document.getElementById('startov');
const FADE_MS = 1000;
let visibleMode = null;
let fadeTimer = null;
let wantVideoPlaying = false;

vid.playsInline = true;
vid.preload = 'auto';
function logVideoEvent(name) {
  console.log(
    '[top] video', name,
    'ready', vid.readyState,
    'network', vid.networkState,
    'paused', vid.paused,
    'time', Number.isFinite(vid.currentTime) ? vid.currentTime.toFixed(2) : 'n/a',
    'src', vid.currentSrc || vid.src
  );
}
for (const eventName of ['loadstart', 'loadedmetadata', 'canplay', 'playing', 'waiting', 'stalled', 'suspend', 'pause', 'ended']) {
  vid.addEventListener(eventName, () => logVideoEvent(eventName));
}
vid.addEventListener('error', () => console.error('[top] video error', vid.error, vid.currentSrc || vid.src));
vid.addEventListener('playing', () => { startov.style.display = 'none'; });

function tryPlay() {
  vid.playsInline = true;
  vid.muted = false;
  vid.volume = 1.0;
  const pr = vid.play();
  if (pr && pr.catch) pr.catch((e) => {
    console.warn('[top] autoplay blocked — click to start with sound:', e && e.name);
    startov.style.display = 'flex';
  });
}
function tryPlaySpeech() {
  ensureSpeechPanner();
  if (speechAudioCtx && speechAudioCtx.state === 'suspended') speechAudioCtx.resume();
  speechAudio.volume = 1.0;
  const pr = speechAudio.play();
  if (pr && pr.catch) pr.catch((e) => {
    console.warn('[top] speech audio blocked — click display once:', e && e.name);
    startov.style.display = 'flex';
  });
}
function ensureSpeechPanner() {
  if (speechPanner || !window.AudioContext && !window.webkitAudioContext) return;
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  speechAudioCtx = new AudioCtx();
  speechSource = speechAudioCtx.createMediaElementSource(speechAudio);
  if (speechAudioCtx.createStereoPanner) {
    speechPanner = speechAudioCtx.createStereoPanner();
    speechSource.connect(speechPanner).connect(speechAudioCtx.destination);
  } else {
    speechPanner = null;
    speechSource.connect(speechAudioCtx.destination);
  }
}
function setSpeechPan(v) {
  ensureSpeechPanner();
  const pan = Math.max(-1, Math.min(1, Number(v) || 0));
  if (speechPanner) speechPanner.pan.value = pan;
}
function retryPlayIfVisible() {
  if (wantVideoPlaying && videoLayer.classList.contains('on') && vid.paused) tryPlay();
}
function clearFadeTimer() {
  if (fadeTimer !== null) {
    clearTimeout(fadeTimer);
    fadeTimer = null;
  }
}
function transitionTo(mode) {
  if (mode === visibleMode) return;
  clearFadeTimer();
  console.log('[top] transition', visibleMode, '->', mode, 'start');

  if (mode === 'topic') {
    fadeCurtain.classList.add('on');
    fadeTimer = setTimeout(() => {
      videoLayer.classList.remove('on');
      topicLayer.classList.add('on');
      wantVideoPlaying = false;
      try { vid.pause(); } catch (e) {}
      visibleMode = 'topic';
      console.log('[top] transition topic visible');
      requestAnimationFrame(() => fadeCurtain.classList.remove('on'));
      fadeTimer = null;
    }, FADE_MS);
    return;
  }

  fadeCurtain.classList.add('on');
  fadeTimer = setTimeout(() => {
    topicLayer.classList.remove('on');
    videoLayer.classList.add('on');
    wantVideoPlaying = true;
    visibleMode = 'video';
    // Stop any lingering robot speech so it cannot bleed into the video audio.
    try { speechAudio.pause(); speechAudio.currentTime = 0; } catch (e) {}
    console.log('[top] transition video visible');
    requestAnimationFrame(() => fadeCurtain.classList.remove('on'));
    requestAnimationFrame(retryPlayIfVisible);
    fadeTimer = null;
  }, FADE_MS);
}
// Any user interaction also kicks playback (covers autoplay-blocked kiosks).
startov.addEventListener('click', () => { startov.style.display = 'none'; tryPlay(); tryPlayBgm(); });
document.addEventListener('pointerdown', () => {
  retryPlayIfVisible();
  tryPlayBgm();
  // Do NOT (re)start speech while a video is showing. The speech element still
  // holds the PREVIOUS turn's clip, so a click during the video would replay
  // the old robot voice mixed into the video audio. Only start speech when a
  // discussion (non-video) is on screen.
  if (speechAudio.src && speechAudio.paused && visibleMode !== 'video') tryPlaySpeech();
}, true);
window.addEventListener('focus', retryPlayIfVisible);
document.addEventListener('visibilitychange', retryPlayIfVisible);
vid.addEventListener('loadedmetadata', retryPlayIfVisible);
vid.addEventListener('canplay', retryPlayIfVisible);

function show(mode) {
  if (visibleMode === null) {
    visibleMode = mode;
    if (mode === 'video') {
      wantVideoPlaying = true;
      videoLayer.classList.add('on');
      requestAnimationFrame(retryPlayIfVisible);
    } else {
      wantVideoPlaying = false;
      topicLayer.classList.add('on');
    }
    return;
  }
  if (mode === 'video') {
    startov.style.display = 'none';
    wantVideoPlaying = true;
    transitionTo('video');
    return;
  }

  startov.style.display = 'none';
  wantVideoPlaying = false;
  transitionTo('topic');
}

function stopAllMedia() {
  clearFadeTimer();
  wantVideoPlaying = false;
  visibleMode = null;
  curSrc = '';
  curEpoch = -1;
  curSpeechEpoch = -1;
  startov.style.display = 'none';
  fadeCurtain.classList.remove('on');
  videoLayer.classList.remove('on');
  topicLayer.classList.remove('on');
  try { vid.pause(); vid.removeAttribute('src'); vid.load(); } catch (e) {}
  try {
    speechAudio.pause();
    speechAudio.removeAttribute('src');
    speechAudio.load();
  } catch (e) {}
  try {
    clearBgmFade();
    bgmAudio.pause();
    bgmAudio.removeAttribute('src');
    bgmAudio.load();
    curBgmSrc = '';
  } catch (e) {}
  gaze.stop();
}

function render(s) {
  console.log('[top] state', s.mode, 'src', s.videoSrc, 'epoch', s.videoEpoch, 'muted', s.muted);
  applyBgm(s);
  applyMute(s);
  show(s.mode);
  if (s.speechAudioSrc && s.speechAudioEpoch !== curSpeechEpoch) {
    curSpeechEpoch = s.speechAudioEpoch;
    setSpeechPan(s.speechPan || 0);
    speechAudio.src = s.speechAudioSrc;
    speechAudio.load();
    tryPlaySpeech();
  }
  if (s.mode === 'video') {
    if (s.videoSrc && s.videoSrc !== curSrc) { curSrc = s.videoSrc; vid.src = s.videoSrc; vid.load(); }
    if (s.videoEpoch !== curEpoch) {
      curEpoch = s.videoEpoch;
      const p = () => { try { vid.currentTime = 0; } catch (e) {} tryPlay(); };
      if (vid.readyState >= 1) p(); else vid.addEventListener('loadedmetadata', p, { once: true });
    }
    requestAnimationFrame(retryPlayIfVisible);
    gaze.stop();
    return;
  }
  // topic mode
  document.getElementById('title').textContent = s.title || '';
  document.getElementById('topic').textContent = s.topic || '';
  for (const role of CFG.roles)
    markerByRole[role].classList.toggle('active', role === s.current);

  // The top display only shows topic + speaker markers; gaze guidance lives on
  // the mini displays so the large screen stays calm.
  gaze.stop();
}

const conn = connectWS('top', (msg) => {
  if (msg.type === 'control' && msg.action === 'stop') {
    stopAllMedia();
    return;
  }
  if (msg.type === 'state') render(msg);
});

// SPACE toggles mute for every display (server echoes the new state back).
window.addEventListener('keydown', (e) => {
  if (e.code === 'Space' || e.key === ' ') {
    e.preventDefault();
    conn.send({ type: 'toggle_mute' });
  }
});

// Tell the server the video finished so playback can advance.
vid.addEventListener('pause', () => {
  if (!wantVideoPlaying) return;
  const duration = Number.isFinite(vid.duration) ? vid.duration : 0;
  const nearEnd = duration > 0 && vid.currentTime >= duration - 0.75;
  if (!nearEnd) setTimeout(retryPlayIfVisible, 120);
});
vid.addEventListener('ended', () => {
  const duration = Number.isFinite(vid.duration) ? vid.duration : 0;
  const nearEnd = duration > 0 && vid.currentTime >= duration - 0.75;
  if (nearEnd) {
    wantVideoPlaying = false;
    conn.send({ type: 'video_ended', currentTime: vid.currentTime, duration });
  } else {
    console.warn('[top] ignoring early ended event', vid.currentTime, duration);
    setTimeout(retryPlayIfVisible, 120);
  }
});

// Operator skip controls (handled fully client-side).
document.addEventListener('keydown', (e) => {
  if (videoLayer.classList.contains('on') && vid.duration) {
    if (e.key === 'ArrowRight') vid.currentTime = Math.min(vid.currentTime + 10, vid.duration - 1);
    if (e.key === 'ArrowLeft') vid.currentTime = Math.max(vid.currentTime - 10, 0);
  }
});
</script>
</body>
</html>
"""


# ── Mini page (one per role) ─────────────────────────────────────────────────

def page_mini(role: str) -> str:
    cfg = json.dumps(_client_config())
    html = _MINI_HTML
    html = html.replace("/*THEME_VARS*/", _theme_vars())
    html = html.replace("/*GAZE_JS*/", _GAZE_JS)
    html = html.replace("/*WS_JS*/", _WS_JS)
    html = html.replace("/*CONFIG*/", cfg)
    html = html.replace("__ROLE__", role)
    return html


_MINI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ethics Podcast — Mini Display</title>
<style>
/*THEME_VARS*/
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: 100%; height: 100%; overflow: hidden; cursor: auto;
  font-family: var(--font); background: var(--snow); }
#bg { position: fixed; inset: 0;
  background: linear-gradient(180deg, var(--white), var(--snow)); }
#glow { position: fixed; inset: 0; opacity: 0.5;
  animation: breathe 4s ease-in-out infinite; }
@keyframes breathe { 0%,100% { opacity: 0.25; } 50% { opacity: 0.7; } }
#content { position: fixed; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 2.7vh; padding: 5vh 6vw;
  opacity: 0.08; transform: translateY(8px) scale(0.985);
  transition: opacity .7s ease, transform .7s ease; }
#content.speaking { opacity: 1; }
#content.speaking { transform: translateY(0) scale(1); }
.speaker-row { display: flex; align-items: center; justify-content: center; gap: 1.2vw; }
.mini-icon { width: 6vh; height: 6vh; display: grid; place-items: center;
  border-radius: 50%; padding: 0.35vh; background: rgba(255,255,255,0.82);
  border: 2px solid var(--role-color);
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.8), 0 0 18px var(--role-glow);
  filter: drop-shadow(0 0 16px var(--role-glow)); }
.mini-icon img { width: 100%; height: 100%; object-fit: cover; border-radius: 50%; display: block; }
.speaker { font-size: 3vh; font-weight: 800; letter-spacing: 0.3vh;
  text-transform: uppercase; }
.subtitle { font-size: 5vh; font-weight: 700; color: var(--ink);
  line-height: 1.3; text-align: center; flex: 0 1 auto;
  display: flex; align-items: center; }
.orb-wrap { width: min(42vh, 54vw); height: min(24vh, 31vw);
  position: relative; display: grid; place-items: center; }
#orb { width: 100%; height: 100%; }
#gaze { position: fixed; inset: 0; display: none; pointer-events: none; }
#hero-arrow { position: fixed; left: 50%; top: 50%; width: 54vmin; height: 42vmin;
  transform: translate(-50%, -50%) rotate(var(--arrow-rot, 0deg)) scale(0.86);
  opacity: 0; z-index: 8; pointer-events: none;
  transition: opacity .65s ease, transform .65s cubic-bezier(.2,.9,.2,1); }
#hero-arrow.on { opacity: 1;
  transform: translate(-50%, -50%) rotate(var(--arrow-rot, 0deg)) scale(1); }
.hero-chevron { position: absolute; left: 50%; top: 50%; width: 44%; height: 54%;
  opacity: 0; transform: translate(-50%, -50%) translateX(-28%) scale(0.92);
  animation: heroFlow 1.65s ease-in-out infinite; }
.hero-chevron:nth-child(2) { animation-delay: .28s; }
.hero-chevron:nth-child(3) { animation-delay: .56s; }
.hero-chevron::before, .hero-chevron::after { content: ""; position: absolute;
  left: 50%; top: 50%; width: 78%; height: 13%; border-radius: 999px;
  background: rgba(var(--arrow-rgb),0.88);
  box-shadow: 0 0 22px rgba(var(--arrow-rgb),0.48), 0 0 54px rgba(var(--arrow-rgb),0.30);
  transform-origin: 88% 50%; }
.hero-chevron::before { transform: translate(-88%, -50%) rotate(42deg); }
.hero-chevron::after { transform: translate(-88%, -50%) rotate(-42deg); }
@keyframes heroFlow {
  0% { opacity: 0; transform: translate(-50%, -50%) translateX(-34%) scale(0.86); }
  45% { opacity: 1; transform: translate(-50%, -50%) translateX(0%) scale(1); }
  100% { opacity: 0; transform: translate(-50%, -50%) translateX(34%) scale(0.86); }
}
</style>
</head>
<body>
  <div id="bg"></div>
  <div id="glow"></div>
  <div id="content">
    <div class="speaker-row">
      <div class="mini-icon" id="speakerIcon"></div>
      <div class="speaker" id="speaker"></div>
    </div>
    <div class="subtitle" id="subtitle"></div>
    <div class="orb-wrap"><canvas id="orb"></canvas></div>
  </div>
  <canvas id="gaze"></canvas>
  <div id="hero-arrow">
    <span class="hero-chevron"></span>
    <span class="hero-chevron"></span>
    <span class="hero-chevron"></span>
  </div>

<script>/*WS_JS*/</script>
<script>/*GAZE_JS*/</script>
<script>
const CFG = /*CONFIG*/;
const ROLE = "__ROLE__";
const accent = CFG.accent[ROLE] || '#1f8bff';
const accentRgb = CFG.accentRgb[ROLE] || CFG.glowRgb;

const content = document.getElementById('content');
const speakerIconEl = document.getElementById('speakerIcon');
const speakerEl = document.getElementById('speaker');
const subtitleEl = document.getElementById('subtitle');
const glowEl = document.getElementById('glow');
const orb = document.getElementById('orb');
const orbCtx = orb.getContext('2d');
const heroArrowEl = document.getElementById('hero-arrow');

// Every mini shows the SAME unified view, so the speaker (name/icon/accent) is
// driven by the server state, not by this page's own role.
let shownSpeaker = null;
function setSpeaker(role) {
  if (!role || role === shownSpeaker) return;
  shownSpeaker = role;
  const a = CFG.accent[role] || accent;
  const rgb = CFG.accentRgb[role] || accentRgb;
  speakerEl.style.color = a;
  speakerEl.textContent = (CFG.names[role] || role).toUpperCase();
  speakerIconEl.style.setProperty('--role-color', a);
  speakerIconEl.style.setProperty('--role-glow', `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.34)`);
  speakerIconEl.innerHTML = `<img src="${CFG.icons[role]}" alt="${CFG.names[role] || role}">`;
  glowEl.style.background =
    `linear-gradient(45deg, rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.13), rgba(0,0,0,0) 60%)`;
  heroArrowEl.style.setProperty('--arrow-rgb', rgb.join(','));
}
setSpeaker(ROLE);  // sensible default before the first state arrives
// Mini displays use the lightweight DOM arrow below. Keep this no-op object so
// state handling stays simple without running a second canvas animation.
const gaze = { start() {}, stop() {}, setDirection() {}, setAccent() {} };

let targetLevel = 0;
let displayLevel = 0;
let orbPhase = 0;
const wave = Array.from({ length: 96 }, () => 0);
const particles = Array.from({ length: 44 }, () => ({
  angle: Math.random() * Math.PI * 2,
  distance: 0.64 + Math.random() * 0.82,
  speed: 0.003 + Math.random() * 0.008,
  size: 1.1 + Math.random() * 2.4,
  alpha: 0.18 + Math.random() * 0.28,
}));

function resizeOrb() {
  const rect = orb.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.floor(rect.width * dpr));
  const h = Math.max(1, Math.floor(rect.height * dpr));
  if (orb.width !== w || orb.height !== h) {
    orb.width = w;
    orb.height = h;
  }
}
window.addEventListener('resize', resizeOrb);

function strokeCircle(ctx, x, y, r, color, width) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
}

function drawOrb() {
  resizeOrb();
  const w = orb.width, h = orb.height;
  const ctx = orbCtx;
  ctx.clearRect(0, 0, w, h);

  displayLevel += (targetLevel - displayLevel) * 0.18;
  orbPhase += 0.018 + displayLevel * 0.02;

  const cx = w / 2;
  const cy = h * 0.5;
  const base = Math.min(w, h) * 0.14;
  const pulse = displayLevel * Math.min(w, h) * 0.04;
  const radius = base + pulse;
  const [r, g, b] = accentRgb;

  const bgGlow = ctx.createRadialGradient(cx, cy, radius * 0.2, cx, cy, radius * 1.95);
  bgGlow.addColorStop(0, `rgba(${r},${g},${b},0.20)`);
  bgGlow.addColorStop(0.45, 'rgba(38,66,170,0.12)');
  bgGlow.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = bgGlow;
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 1.95, 0, Math.PI * 2);
  ctx.fill();

  ctx.lineCap = 'round';
  for (let i = 0; i < 4; i++) {
    const ring = base * (1.22 + i * 0.25) + Math.sin(orbPhase * 2 + i) * 2.5;
    strokeCircle(ctx, cx, cy, ring, `rgba(${r},${g},${b},${0.25 - i * 0.035})`, 1.5);
  }

  for (let i = 0; i < 12; i++) {
    const a = orbPhase * 1.65 + i * Math.PI * 2 / 12;
    const len = 0.18 + displayLevel * 0.13;
    const rr = base * 1.55;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr);
    ctx.lineTo(cx + Math.cos(a + len) * rr, cy + Math.sin(a + len) * rr);
    ctx.strokeStyle = `rgba(${r},${g},${b},0.54)`;
    ctx.lineWidth = 2.2;
    ctx.stroke();
  }

  for (let i = 0; i < particles.length; i++) {
    const p = particles[i];
    p.angle += p.speed + displayLevel * 0.014;
    const px = cx + Math.cos(p.angle) * base * p.distance;
    const py = cy + Math.sin(p.angle) * base * p.distance;
    const size = p.size + displayLevel * 2.5;
    ctx.fillStyle = `rgba(${r},${g},${b},${Math.min(0.72, p.alpha + displayLevel * 0.42)})`;
    ctx.beginPath();
    ctx.arc(px, py, size, 0, Math.PI * 2);
    ctx.fill();
  }

  const glow = ctx.createRadialGradient(cx, cy, radius * 0.25, cx, cy, radius * 1.48);
  glow.addColorStop(0, `rgba(${r},${g},${b},0.42)`);
  glow.addColorStop(0.45, `rgba(${r},${g},${b},0.18)`);
  glow.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 1.48, 0, Math.PI * 2);
  ctx.fill();

  const body = ctx.createRadialGradient(cx - radius * 0.26, cy - radius * 0.35, radius * 0.08, cx, cy, radius * 1.28);
  body.addColorStop(0, 'rgba(55,72,148,0.96)');
  body.addColorStop(0.48, 'rgba(14,20,65,0.97)');
  body.addColorStop(0.78, 'rgba(7,13,38,0.98)');
  body.addColorStop(1, `rgba(${r},${g},${b},0.82)`);
  ctx.fillStyle = body;
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fill();
  strokeCircle(ctx, cx, cy, radius, `rgba(${r},${g},${b},0.82)`, 2.5);
  strokeCircle(ctx, cx, cy, radius * 0.72, 'rgba(14,28,88,0.58)', 2);

  wave.push((Math.random() * 2 - 1) * 0.65 * displayLevel + Math.sin(orbPhase * 5) * 0.32 * displayLevel);
  wave.shift();
  const waveW = radius * 1.48;
  const startX = cx - waveW / 2;
  ctx.beginPath();
  for (let i = 0; i < wave.length; i++) {
    const x = startX + (waveW * i) / Math.max(1, wave.length - 1);
    const y = cy + wave[i] * radius * 0.34 + Math.sin(i * 0.32 + orbPhase * 4) * displayLevel * radius * 0.08;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = `rgba(${r},${g},${b},0.94)`;
  ctx.lineWidth = Math.max(2, Math.min(w, h) * 0.008);
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(cx - radius * 0.28, cy - radius * 0.36, radius * 0.16, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(255,255,255,0.16)';
  ctx.fill();

  requestAnimationFrame(drawOrb);
}
requestAnimationFrame(drawOrb);

function setLevel(v) {
  targetLevel = Math.max(0, Math.min(1, Number(v) || 0));
}
setLevel(0);

function showHeroArrow(dir) {
  heroArrowEl.style.setProperty('--arrow-rot', dir < 0 ? '180deg' : '0deg');
  heroArrowEl.classList.add('on');
}

function hideHeroArrow() {
  heroArrowEl.classList.remove('on');
}

function stopAllMedia() {
  content.classList.remove('speaking');
  setLevel(0);
  hideHeroArrow();
  gaze.stop();
}

// This mini sits to the RIGHT of its namesake robot, i.e. between that robot
// and the next one. Robots are at integer positions (0,1,2 left->right).
const MINI_POS = (CFG.position[ROLE] ?? 0) + 0.5;
function arrowDirToward(role) {
  const sp = CFG.position[role];
  if (sp == null) return 0;
  return sp > MINI_POS ? 1 : (sp < MINI_POS ? -1 : 0);   // +1 right, -1 left
}

function render(s) {
  if (s.state === 'speaking') {
    setSpeaker(s.speaker);          // who is talking (subtitle same on every mini)
    content.classList.add('speaking');
    subtitleEl.textContent = s.text || '';
    setLevel(0.4);                  // default until live levels arrive
    hideHeroArrow();
  } else if (s.state === 'arrow') {
    // Transition: fade the subtitle out (CSS handles the fade) and point the
    // arrow toward the next speaker, from THIS mini's physical position.
    content.classList.remove('speaking');
    setLevel(0);
    const dir = arrowDirToward(s.speaker);
    if (dir !== 0) showHeroArrow(dir); else hideHeroArrow();
  } else {  // idle
    content.classList.remove('speaking');
    setLevel(0);
    hideHeroArrow();
  }
}

connectWS(ROLE, (msg) => {
  if (msg.type === 'control' && msg.action === 'stop') {
    stopAllMedia();
    return;
  }
  if (msg.type === 'state') render(msg);
  else if (msg.type === 'level') setLevel(msg.value);
});
</script>
</body>
</html>
"""


# ── Tiled "all" page + index (single-machine testing / convenience) ──────────

def page_all() -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Ethics Podcast — All Displays</title><style>"
        "*{margin:0;padding:0;box-sizing:border-box}"
        "html,body{width:100%;height:100%;overflow:hidden;background:#0b1622}"
        ".grid{display:grid;grid-template-columns:30% 1fr;height:100%;gap:6px;padding:6px}"
        ".top{grid-row:span 2;width:100%;height:100%;border:0;border-radius:8px}"
        ".mini{width:100%;height:100%;border:0;border-radius:8px}"
        ".right{display:grid;grid-template-rows:1fr 1fr;gap:6px}"
        "</style></head><body><div class='grid'>"
        '<iframe src="/top" class="top" allow="autoplay; fullscreen"></iframe>'
        "<div class='right'>"
        # 3-display setup: one big top + two identical mini screens. Both minis
        # show the same unified view, so any two routes work.
        + "".join(
            f'<iframe src="/{r}" class="mini" allow="autoplay"></iframe>'
            for r in ROLES[:2]
        )
        + "</div></div></body></html>"
    )


def page_index(urls: list[str]) -> str:
    items = "".join(f'<li><a href="{u}">{u}</a></li>' for u in urls)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Ethics Podcast Displays</title><style>"
        "body{font-family:system-ui,sans-serif;background:#f4f8ff;color:#0b2a4a;"
        "padding:40px;font-size:18px}a{color:#0a5bd6}li{margin:8px 0}"
        "</style></head><body><h1>Ethics Podcast — Displays</h1><ul>"
        + items + "</ul></body></html>"
    )
