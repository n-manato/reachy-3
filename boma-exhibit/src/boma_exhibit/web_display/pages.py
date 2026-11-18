"""The HTML served to the TV.

One page, no static files, no build step: CSS and JS are inlined and the theme
is injected from theme.py so colours and names have a single source of truth.

The layout has to carry something the reference exhibit did not: captions. Two
thirds of this sketch is spoken in Swahili and Fulfulde, so every line appears
in its original language with an English gloss underneath, and the language is
named on the speaking robot's badge.
"""

from __future__ import annotations

import json

from boma_exhibit import theme
from boma_exhibit.config import ROLE_ORDER


def _client_config() -> dict:
    """Theme values the browser JS needs, derived from theme.py."""
    return {
        "roles": list(ROLE_ORDER),
        "names": theme.ROLE_NAMES,
        "languages": theme.ROLE_LANGUAGE,
        "icons": {role: f"/icon/{role}" for role in ROLE_ORDER},
        "accent": theme.ROLE_ACCENT,
        "accentRgb": {role: list(theme.role_accent_rgb(role)) for role in ROLE_ORDER},
        "position": theme.ROLE_POSITION,
        "appTitle": theme.APP_TITLE,
    }


def _theme_vars() -> str:
    return f"""
      --white: {theme.WHITE};
      --cream: {theme.CREAM};
      --sand: {theme.SAND};
      --ink: {theme.INK};
      --ink-soft: {theme.INK_SOFT};
      --line: {theme.LINE};
      --accent: {theme.ACCENT};
      --accent-deep: {theme.ACCENT_DEEP};
      --accent-glow: {theme.ACCENT_GLOW};
      --font: {theme.FONT_FAMILY};
    """


_TOP_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BOMA — Three Robots, One Africa</title>
<style>
:root { /*THEME_VARS*/ }

* { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  width: 100%; height: 100%; overflow: hidden;
  background: var(--cream);
  font-family: var(--font);
  color: var(--ink);
  -webkit-font-smoothing: antialiased;
}

#stage {
  position: fixed; inset: 0;
  display: flex; flex-direction: column; align-items: center;
  padding: 4vmin 4vmin 5vmin;
  background: linear-gradient(180deg, var(--white) 0%, var(--cream) 45%, var(--sand) 100%);
}

/* A thin inset frame, so the page reads as a designed card on a big screen. */
#stage::before {
  content: ""; position: absolute; inset: 1.8vmin;
  border: 2px solid rgba(200, 69, 43, 0.18);
  border-radius: 2.4vmin; pointer-events: none;
}

.app-title {
  color: var(--accent);
  font-size: clamp(14px, 1.9vmin, 34px);
  font-weight: 900;
  letter-spacing: 0.42em;
  text-align: center;
  text-transform: uppercase;
  flex: 0 0 auto;
}

/* ── The three robots ─────────────────────────────────────────────────── */

.markers {
  display: flex; width: 100%;
  justify-content: space-evenly; align-items: flex-end;
  gap: 2vmin;
  margin-top: 3vmin;
  flex: 0 0 auto;
}

.marker {
  display: flex; flex-direction: column; align-items: center;
  gap: 1.4vmin; min-width: 0;
}

.robot-icon {
  width: min(22vw, 30vh); height: min(22vw, 30vh);
  min-width: 90px; min-height: 90px;
  display: grid; place-items: center;
  border-radius: 50%; padding: 0.4vmin;
  background: rgba(255, 255, 255, 0.82);
  border: 2px solid var(--line);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.7);
  transform: scale(0.87); opacity: 0.62;
  transition: transform .35s ease, border-color .35s ease,
              opacity .35s ease, box-shadow .35s ease, filter .35s ease;
}
.robot-icon img {
  width: 100%; height: 100%;
  object-fit: cover; border-radius: 50%; display: block;
}

.name {
  font-size: clamp(13px, 1.9vmin, 34px);
  font-weight: 700; letter-spacing: 0.16em;
  color: var(--ink-soft);
  transition: color .35s ease, font-weight .35s ease;
  white-space: nowrap;
}

.lang {
  font-size: clamp(10px, 1.3vmin, 22px);
  font-weight: 600; letter-spacing: 0.1em;
  color: var(--ink-soft); opacity: 0.65;
  padding: 0.35em 0.9em; border-radius: 999px;
  border: 1px solid var(--line);
  transition: all .35s ease;
  white-space: nowrap;
}

/* Speaking. The whole point of the screen: who is talking right now. */
.marker.active .robot-icon {
  transform: scale(1.06); opacity: 1;
  border-color: var(--role-color);
  box-shadow: inset 0 0 0 2px rgba(255,255,255,0.9), 0 0 2.6vmin var(--role-glow);
  filter: drop-shadow(0 0 1.8vmin var(--role-glow));
}
.marker.active .name { color: var(--ink); font-weight: 900; }
.marker.active .lang {
  color: var(--white); opacity: 1;
  background: var(--role-color); border-color: var(--role-color);
}

/* About to speak: a gentle lift, clearly weaker than `active`. */
.marker.next .robot-icon { transform: scale(0.95); opacity: 0.85; }
.marker.next .name { color: var(--ink); }

/* ── Captions ─────────────────────────────────────────────────────────── */

#caption-wrap {
  flex: 1 1 auto; width: 100%;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  gap: 2.2vmin; padding: 3vmin 2vmin 0;
  min-height: 0;
}

.caption {
  font-size: clamp(20px, 4.4vmin, 92px);
  font-weight: 800; line-height: 1.22;
  text-align: center; color: var(--ink);
  max-width: 88%;
  transition: opacity .28s ease;
}

.caption-sub {
  font-size: clamp(14px, 2.5vmin, 52px);
  font-weight: 500; font-style: italic; line-height: 1.3;
  text-align: center; color: var(--ink-soft);
  max-width: 78%;
  transition: opacity .28s ease;
}

/* Attract screen: the title takes the space the captions use during the show. */
.attract-title {
  font-size: clamp(26px, 6vmin, 120px);
  font-weight: 900; line-height: 1.1;
  text-align: center; color: var(--accent-deep);
  max-width: 92%;
}
.attract-sub {
  font-size: clamp(14px, 2.6vmin, 54px);
  font-weight: 500; text-align: center; color: var(--ink-soft);
  max-width: 80%;
}

.hidden { display: none !important; }

/* Stopping fades the words out with the sound, rather than blanking them. */
#caption-wrap { transition: opacity .7s ease; }
#caption-wrap.fading { opacity: 0; }

/* ── Transport ────────────────────────────────────────────────────────── */
/* The buttons live on this page, not on a separate operator page, because a
   browser only unlocks audio for the document that received the click -- and
   this document is the one that has to make the sound. */

#transport {
  position: fixed; left: 50%; bottom: 3.2vmin; z-index: 60;
  transform: translateX(-50%);
  display: flex; gap: 1.4vmin;
  padding: 1.1vmin 1.4vmin; border-radius: 999px;
  background: rgba(42, 29, 20, 0.82);
  box-shadow: 0 0.6vmin 2.4vmin rgba(42, 29, 20, 0.28);
  opacity: 1; transition: opacity .45s ease;
}
#transport.idle { opacity: 0; pointer-events: none; }

/* The pointer goes away with the controls. On a kiosk screen an abandoned
   cursor sitting over the stage is the one thing that gives away that this is
   a web page. Applied to descendants too, so the buttons' own cursor:pointer
   cannot leave an arrow behind. */
body.hide-cursor, body.hide-cursor * { cursor: none; }

#transport button {
  display: flex; align-items: center; gap: 0.7em;
  border: 0; border-radius: 999px; cursor: pointer;
  padding: 0.62em 1.5em;
  font-family: var(--font);
  font-size: clamp(12px, 1.6vmin, 28px); font-weight: 800;
  letter-spacing: 0.08em;
  color: var(--white); background: rgba(255, 255, 255, 0.14);
  transition: background .2s ease, transform .12s ease, opacity .2s ease;
}
#transport button:hover:not(:disabled) { background: rgba(255, 255, 255, 0.26); }
#transport button:active:not(:disabled) { transform: scale(0.96); }
#transport button:disabled { opacity: 0.34; cursor: default; }
#transport #btn-play:not(:disabled) { background: var(--accent); }

#transport svg { width: 1.25em; height: 1.25em; flex: 0 0 auto; }

/* Loop mode. Like a music player: one button, cycling through the modes, its
   label saying which one is live. */
#btn-loop { min-width: 9.5em; justify-content: center; }
#btn-loop.mode-one { background: var(--accent-glow); color: var(--ink); }
#btn-loop.mode-sequential { background: rgba(255, 255, 255, 0.30); }

/* Volume. Belongs to this screen, not to the show: it is the browser that
   makes the sound, so the level is stored here and survives a restart. */
#volume-wrap {
  display: flex; align-items: center; gap: 0.7em;
  padding: 0 1.1em 0 1.3em; color: var(--white);
}
#volume {
  -webkit-appearance: none; appearance: none;
  width: clamp(70px, 9vmin, 170px); height: 0.42em;
  border-radius: 999px; background: rgba(255, 255, 255, 0.26);
  outline: none; cursor: pointer;
}
#volume::-webkit-slider-thumb {
  -webkit-appearance: none; appearance: none;
  width: 1.15em; height: 1.15em; border-radius: 50%;
  background: var(--white); border: 0;
  box-shadow: 0 0 0.5vmin rgba(0, 0, 0, 0.35);
}
#volume::-moz-range-thumb {
  width: 1.15em; height: 1.15em; border-radius: 50%;
  background: var(--white); border: 0;
}
#volume-wrap.muted { opacity: 0.5; }

#muted-badge {
  position: fixed; top: 3vmin; right: 3.4vmin; z-index: 40;
  padding: 0.5em 1.1em; border-radius: 999px;
  background: var(--accent-deep); color: var(--white);
  font-size: clamp(11px, 1.5vmin, 26px); font-weight: 700;
  letter-spacing: 0.1em; display: none;
}
#muted-badge.show { display: block; }
</style>
</head>
<body>
  <div id="stage">
    <div class="app-title" id="app-title"></div>
    <div class="markers" id="markers"></div>

    <div id="caption-wrap">
      <div class="attract-title hidden" id="attract-title"></div>
      <div class="attract-sub hidden" id="attract-sub"></div>
      <div class="caption hidden" id="caption"></div>
      <div class="caption-sub hidden" id="caption-sub"></div>
    </div>
  </div>

  <div id="muted-badge">MUTED</div>

  <div id="transport">
    <div id="volume-wrap">
      <svg id="vol-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M4 9v6h4l5 4V5L8 9H4z"/>
        <path id="vol-wave1" d="M15.5 8.5a4.5 4.5 0 010 7" fill="none"
              stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
        <path id="vol-wave2" d="M18.5 5.5a8.5 8.5 0 010 13" fill="none"
              stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
      </svg>
      <input id="volume" type="range" min="0" max="100" step="1" value="100"
             aria-label="Volume">
    </div>
    <button id="btn-play" type="button">▶ PLAY</button>
    <button id="btn-stop" type="button">■ STOP</button>
    <button id="btn-loop" type="button" title="Loop mode">
      <svg id="loop-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
           aria-hidden="true"></svg>
      <span id="loop-label">SHUFFLE</span>
    </button>
  </div>

  <audio id="speech" preload="auto"></audio>
  <audio id="bgm" preload="auto" loop></audio>

<script>
const CFG = /*CONFIG*/;

/* ── Build the three robot markers, left to right ────────────────────── */
const markersEl = document.getElementById('markers');
const markerByRole = {};
document.getElementById('app-title').textContent = CFG.appTitle;

const ordered = [...CFG.roles].sort((a, b) => CFG.position[a] - CFG.position[b]);
for (const role of ordered) {
  const rgb = (CFG.accentRgb[role] || [0, 0, 0]).join(',');
  const el = document.createElement('div');
  el.className = 'marker';
  el.style.setProperty('--role-color', CFG.accent[role]);
  el.style.setProperty('--role-glow', `rgba(${rgb},0.45)`);
  el.innerHTML =
    `<div class="robot-icon"><img src="${CFG.icons[role]}" alt="${CFG.names[role]}"></div>` +
    `<div class="name">${CFG.names[role]}</div>` +
    `<div class="lang">${CFG.languages[role]}</div>`;
  markersEl.appendChild(el);
  markerByRole[role] = el;
}

/* ── Audio ───────────────────────────────────────────────────────────── */
const speech = document.getElementById('speech');
const bgm = document.getElementById('bgm');
const mutedBadge = document.getElementById('muted-badge');

const FADE_MS = 700;

let audioCtx = null, speechPanner = null, speechGain = null, masterGain = null;
let masterVolume = 1;
let curSpeechEpoch = -1, curBgmEpoch = -1, curBgmSrc = '';

/* ── Background music envelope ────────────────────────────────────────────
   The bed ducks out of the way whenever a robot speaks and drifts back up
   between turns. It is driven by the speech element's own events rather than
   by messages from the server, so it is exactly in step with the audio the
   room is actually hearing -- and costs no extra traffic on the link the
   robots need for motion.

   Asymmetric on purpose: dip fast so the first syllable is never buried, come
   back slowly so 34 turns do not turn into 34 audible swells. */
const BGM_DUCK = 0.30;         // level while someone is talking
const BGM_DUCK_MS = 300;
const BGM_RELEASE_MS = 1400;
const BGM_FADE_IN_MS = 3000;   // the show starting, not a turn ending

let bgmLevel = 0;
let bgmRamp = null;
let bgmTicking = false;
let speechActive = false;

/* Declared here, not down in the transport section: the envelope below reads it
   and runs first. */
let playing = false;

/* The graph: source -> panner -> fade gain -> master gain -> speakers.
   The panner puts each robot's voice where that robot is standing, so the
   surround system places it on the correct side of the stage. The two gains are
   kept separate on purpose: Stop ramps the fade gain to zero, and the operator
   sets the master. One node doing both would mean a Stop wipes out the volume
   setting, and a volume change mid-fade cancels the fade. */
function ensureAudioGraph() {
  if (speechGain || !window.AudioContext) return;
  try {
    audioCtx = new AudioContext();
    const source = audioCtx.createMediaElementSource(speech);
    speechGain = audioCtx.createGain();
    masterGain = audioCtx.createGain();
    masterGain.gain.value = masterVolume;
    let head = source;
    if (audioCtx.createStereoPanner) {
      speechPanner = audioCtx.createStereoPanner();
      source.connect(speechPanner);
      head = speechPanner;
    }
    head.connect(speechGain).connect(masterGain).connect(audioCtx.destination);
  } catch (e) { console.warn('[top] web audio unavailable', e); }
}

function setPan(pan) {
  ensureAudioGraph();
  if (speechPanner) speechPanner.pan.value = Math.max(-1, Math.min(1, pan || 0));
}

/* Always call this before starting a clip. A Stop leaves the gain ramping to
   zero, and a Play a moment later would otherwise be silent. */
function resetAudioGain() {
  if (speechGain && audioCtx) {
    speechGain.gain.cancelScheduledValues(audioCtx.currentTime);
    speechGain.gain.setValueAtTime(1, audioCtx.currentTime);
  }
  /* With a graph the element runs at full scale and masterGain does the
     attenuating; without one the element itself has to carry the volume. */
  speech.volume = masterGain ? 1 : masterVolume;
}

function fadeOutAudio() {
  if (speechGain && audioCtx) {
    const now = audioCtx.currentTime;
    speechGain.gain.cancelScheduledValues(now);
    speechGain.gain.setValueAtTime(speechGain.gain.value, now);
    speechGain.gain.linearRampToValueAtTime(0.0001, now + FADE_MS / 1000);
  } else {
    /* No Web Audio: ramp the element itself. */
    stepVolumeDown(speech);
  }
  setTimeout(() => { try { speech.pause(); } catch (e) {} }, FADE_MS);

  speechActive = false;
  rampBgm(FADE_MS);   // playing is already false, so this targets silence
  setTimeout(() => { try { bgm.pause(); } catch (e) {} }, FADE_MS + 60);
}

function bgmTargetLevel() {
  if (!playing || !bgm.src) return 0;
  const base = bgmBaseVolume * masterVolume;
  return speechActive ? base * BGM_DUCK : base;
}

function rampBgm(ms) {
  const to = bgmTargetLevel();
  if (ms <= 0 || Math.abs(to - bgmLevel) < 0.0005) {
    bgmRamp = null;
    bgmLevel = to;
    try { bgm.volume = Math.max(0, Math.min(1, to)); } catch (e) {}
    return;
  }
  bgmRamp = { from: bgmLevel, to, t0: performance.now(), ms };
  if (!bgmTicking) { bgmTicking = true; requestAnimationFrame(tickBgm); }
}

function tickBgm() {
  if (!bgmRamp) { bgmTicking = false; return; }
  const k = Math.min(1, (performance.now() - bgmRamp.t0) / bgmRamp.ms);
  bgmLevel = bgmRamp.from + (bgmRamp.to - bgmRamp.from) * k;
  try { bgm.volume = Math.max(0, Math.min(1, bgmLevel)); } catch (e) {}
  if (k >= 1) { bgmRamp = null; bgmTicking = false; return; }
  requestAnimationFrame(tickBgm);
}

/* The bed follows the voice. 'playing' fires when audio actually starts, not
   when it is merely requested, so the duck lands with the sound. */
speech.addEventListener('playing', () => { speechActive = true; rampBgm(BGM_DUCK_MS); });
speech.addEventListener('ended', () => { speechActive = false; rampBgm(BGM_RELEASE_MS); });
speech.addEventListener('pause', () => { speechActive = false; rampBgm(BGM_RELEASE_MS); });

/* Speech is inside the Web Audio graph, so its fade is a gain ramp. This is the
   fallback for browsers with no AudioContext. */
function stepVolumeDown(el, done) {
  const start = el.volume;
  const t0 = performance.now();
  const timer = setInterval(() => {
    const k = Math.min(1, (performance.now() - t0) / FADE_MS);
    el.volume = Math.max(0, start * (1 - k));
    if (k >= 1) { clearInterval(timer); if (done) done(); }
  }, 40);
}

function playSpeech() {
  const p = speech.play();
  if (p && p.catch) p.catch((e) => console.warn('[top] speech play refused', e));
}

/* ── Transport ───────────────────────────────────────────────────────── */
const transport = document.getElementById('transport');
const btnPlay = document.getElementById('btn-play');
const btnStop = document.getElementById('btn-stop');
const btnLoop = document.getElementById('btn-loop');
const loopIcon = document.getElementById('loop-icon');
const loopLabel = document.getElementById('loop-label');
const volume = document.getElementById('volume');
const volumeWrap = document.getElementById('volume-wrap');
const volWave1 = document.getElementById('vol-wave1');
const volWave2 = document.getElementById('vol-wave2');

/* ── Volume ──────────────────────────────────────────────────────────── */
const VOLUME_KEY = 'boma.volume';
let bgmBaseVolume = 0.18;

function applyVolume(v, save) {
  masterVolume = Math.max(0, Math.min(1, v));
  if (masterGain) masterGain.gain.value = masterVolume;
  else speech.volume = masterVolume;
  rampBgm(0);   // follow the slider immediately, keeping any duck in place

  volume.value = Math.round(masterVolume * 100);
  volumeWrap.classList.toggle('muted', masterVolume === 0);
  /* The waves drop away as the level falls, so the icon reads at a glance. */
  volWave1.style.opacity = masterVolume > 0.02 ? '1' : '0';
  volWave2.style.opacity = masterVolume > 0.55 ? '1' : '0';

  if (save) {
    try { localStorage.setItem(VOLUME_KEY, String(masterVolume)); } catch (e) {}
    // The robots may be the ones making the sound, in which case this slider
    // only means anything if the server hears about it.
    send({ type: 'volume', value: masterVolume });
  }
}

/* Restore the level before anything can play. The page reloads on every server
   restart, and having to reset the volume each time would be miserable. */
(function restoreVolume() {
  let stored = null;
  try { stored = localStorage.getItem(VOLUME_KEY); } catch (e) {}
  const v = stored === null ? 1 : parseFloat(stored);
  applyVolume(Number.isFinite(v) ? v : 1, false);
})();

volume.addEventListener('input', () => applyVolume(volume.value / 100, true));

/* ── Loop mode ───────────────────────────────────────────────────────── */
const LOOP_ICONS = {
  shuffle:
    '<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/>' +
    '<polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/>' +
    '<line x1="4" y1="4" x2="9" y2="9"/>',
  sequential:
    '<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/>' +
    '<polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
  one:
    '<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/>' +
    '<polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>' +
    '<path d="M10.6 10.9 12.4 9.7v4.9"/>',
};
const LOOP_LABELS = { shuffle: 'SHUFFLE', sequential: 'ALL', one: 'ONE' };

let loopMode = null;

function applyLoopMode(mode) {
  if (mode === loopMode) return;
  loopMode = mode;
  loopIcon.innerHTML = LOOP_ICONS[mode] || LOOP_ICONS.shuffle;
  loopLabel.textContent = LOOP_LABELS[mode] || mode;
  btnLoop.classList.remove('mode-shuffle', 'mode-sequential', 'mode-one');
  btnLoop.classList.add('mode-' + mode);
}

btnLoop.addEventListener('click', () => send({ type: 'cycle_loop' }));

const captionWrap = document.getElementById('caption-wrap');

let hideTimer = null;
let audioUnlocked = false;

function send(msg) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(msg));
  }
}

btnPlay.addEventListener('click', () => {
  /* This click is the user gesture browsers require before a page may play
     audio -- which is why there is no separate click-to-start overlay. */
  ensureAudioGraph();
  if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
  resetAudioGain();
  audioUnlocked = true;
  btnPlay.disabled = playing;
  send({ type: 'play' });
});

btnStop.addEventListener('click', () => send({ type: 'stop' }));

/* Video-player behaviour: visible while stopped (there is nothing else to look
   at, and the operator needs to find Play), fading away during a show unless
   someone is actually using the screen. */
function nudgeControls() {
  transport.classList.remove('idle');
  document.body.classList.remove('hide-cursor');
  if (hideTimer) clearTimeout(hideTimer);
  if (!playing) return;   // stopped: leave them up so PLAY can be found
  hideTimer = setTimeout(() => {
    transport.classList.add('idle');
    document.body.classList.add('hide-cursor');
  }, 3000);
}
for (const evt of ['pointermove', 'pointerdown', 'keydown']) {
  document.addEventListener(evt, nudgeControls);
}

function applyTransport(state) {
  const wasPlaying = playing;
  playing = state === 'playing';
  /* Play stays clickable until this page has actually been allowed to make a
     sound. Reloading mid-show gets you a page that believes it is playing but
     has never had a user gesture, so its audio is blocked -- leaving Play
     greyed out would strand the operator with a silent screen and no way back
     except Stop-then-Play. */
  btnPlay.disabled = playing && audioUnlocked;
  btnStop.disabled = !playing;

  if (playing && !wasPlaying) {
    captionWrap.classList.remove('fading');
    resetAudioGain();
  }
  if (!playing && wasPlaying) {
    captionWrap.classList.add('fading');
    fadeOutAudio();
  }

  /* Only on an actual transport change. render() runs on every state message,
     so nudging unconditionally made the controls pop back into view each time
     the speaker changed -- roughly every few seconds, all show long. */
  if (playing !== wasPlaying) nudgeControls();
}

/* Reflect the stopped state immediately, before the first server message. */
applyTransport('stopped');

/* ── Render ──────────────────────────────────────────────────────────── */
const attractTitle = document.getElementById('attract-title');
const attractSub = document.getElementById('attract-sub');
const caption = document.getElementById('caption');
const captionSub = document.getElementById('caption-sub');

function show(el, text) {
  el.textContent = text || '';
  el.classList.toggle('hidden', !text);
}

function render(s) {
  applyTransport(s.transport || 'stopped');
  applyLoopMode(s.loopMode || 'shuffle');

  const current = s.current || [];
  const next = s.next || [];
  for (const role of CFG.roles) {
    const el = markerByRole[role];
    el.classList.toggle('active', current.includes(role));
    el.classList.toggle('next', !current.includes(role) && next.includes(role));
  }

  if (s.mode === 'attract') {
    /* The wind-down is over once the server switches to the attract screen, so
       lift the fade and let the title come back up through it. */
    captionWrap.classList.remove('fading');
    show(attractTitle, s.title);
    show(attractSub, s.subtitle);
    show(caption, '');
    show(captionSub, '');
  } else {
    show(attractTitle, '');
    show(attractSub, '');
    show(caption, s.text);
    show(captionSub, s.translation);
  }

  mutedBadge.classList.toggle('show', !!s.muted);
  speech.muted = !!s.muted;
  bgm.muted = !!s.muted;

  if (playing && s.speechAudioSrc && s.speechAudioEpoch !== curSpeechEpoch) {
    curSpeechEpoch = s.speechAudioEpoch;
    setPan(s.speechPan);
    resetAudioGain();   // a previous Stop may have left the gain ramped away
    speech.src = s.speechAudioSrc;
    speech.load();
    playSpeech();
  }

  if (s.bgmSrc && playing) {
    bgmBaseVolume = s.bgmVolume != null ? s.bgmVolume : 0.18;
    if (s.bgmSrc !== curBgmSrc || s.bgmEpoch !== curBgmEpoch) {
      curBgmSrc = s.bgmSrc; curBgmEpoch = s.bgmEpoch;
      bgm.src = s.bgmSrc; bgm.load();
      /* Come up out of silence over a few seconds. Landing at full level the
         instant the show starts announces itself; this way the room simply
         notices, a little late, that music is playing. */
      bgmLevel = 0; bgm.volume = 0;
      bgm.play().catch(() => {});
      rampBgm(BGM_FADE_IN_MS);
    } else {
      rampBgm(BGM_DUCK_MS);   // level or duck state may have moved
    }
  } else if (curBgmSrc) {
    curBgmSrc = ''; bgmLevel = 0; bgm.pause();
    bgm.removeAttribute('src'); bgm.load();
  }
}

/* ── WebSocket ───────────────────────────────────────────────────────── */
let bootId = null;
let socket = null;

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${proto}://${location.host}/ws/top`);

  socket.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    /* A new program run means a new boot id. Reload so a display left open
       overnight picks up the new build instead of showing stale state. */
    if (msg.bootId) {
      if (bootId === null) bootId = msg.bootId;
      else if (bootId !== msg.bootId) { location.reload(); return; }
    }
    if (msg.type === 'state') render(msg);
  };

  socket.onclose = () => setTimeout(connect, 1000);
  socket.onerror = () => { try { socket.close(); } catch (e) {} };
}
connect();

document.addEventListener('keydown', (e) => {
  if (e.code !== 'Space') return;
  e.preventDefault();
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'toggle_mute' }));
  }
});
</script>
</body>
</html>
"""


_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>BOMA Exhibit</title>
<style>
  body { font-family: /*FONT*/; background: /*CREAM*/; color: /*INK*/;
         display: grid; place-items: center; height: 100vh; margin: 0; }
  a { color: /*ACCENT*/; font-size: 1.5rem; font-weight: 700; }
  p { color: /*INK_SOFT*/; margin-top: 1rem; }
</style>
</head>
<body>
  <div>
    <a href="/top">Open the exhibit display &rarr;</a>
    <p>On the TV, open <code>/*URL*/</code> full-screen.</p>
  </div>
</body>
</html>
"""


def page_top() -> str:
    html = _TOP_HTML
    html = html.replace("/*THEME_VARS*/", _theme_vars())
    html = html.replace("/*CONFIG*/", json.dumps(_client_config()))
    return html


def page_index(top_url: str) -> str:
    html = _INDEX_HTML
    html = html.replace("/*FONT*/", theme.FONT_FAMILY)
    html = html.replace("/*CREAM*/", theme.CREAM)
    html = html.replace("/*INK_SOFT*/", theme.INK_SOFT)
    html = html.replace("/*INK*/", theme.INK)
    html = html.replace("/*ACCENT*/", theme.ACCENT)
    html = html.replace("/*URL*/", top_url)
    return html
