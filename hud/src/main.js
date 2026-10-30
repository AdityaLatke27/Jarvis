import * as THREE from 'three';
import './style.css';
import { AudioEngine } from './audio.js';
import { Hologram } from './hologram.js';
import { createPost } from './post.js';
import { HUD } from './hud.js';
import { JarvisLink } from './link.js';

// ---------------------------------------------------------------- renderer
const canvas = document.getElementById('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: 'high-performance' });
const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
renderer.setPixelRatio(pixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x020407, 1);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.95;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.1, 200);

const audio = new AudioEngine();
const spectrumTex = new THREE.DataTexture(audio.specBytes, audio.specBytes.length, 1, THREE.RedFormat, THREE.UnsignedByteType);
spectrumTex.minFilter = THREE.LinearFilter;
spectrumTex.magFilter = THREE.LinearFilter;
spectrumTex.needsUpdate = true;

const holo = new Hologram(spectrumTex);
holo.u.uPixelRatio.value = pixelRatio;
scene.add(holo.group);

const post = createPost(renderer, scene, camera);
const hud = new HUD();

// ------------------------------------------------------------- interaction
const state = {
  ndc: new THREE.Vector2(),
  smooth: new THREE.Vector2(),
  last: new THREE.Vector2(),
  inside: false,
  dragging: false,
  moved: 0,
  rot: new THREE.Vector2(),
  vel: new THREE.Vector2(),
  baseDist: 5.2,
  zoom: 1,
  zoomTarget: 1,
  lastMove: -99,
  mouseForce: 0,
  hovered: false,
  hit: new THREE.Vector3(),
  energy: 0,
  glitch: 0,
  busy: false, // JARVIS is transcribing/thinking: keep the core energized
};
const raycaster = new THREE.Raycaster();
const hitSphere = new THREE.Sphere(new THREE.Vector3(), 1.12);
let time = 0;

const isUI = (el) => !!el?.closest?.('button, input, a, form, .modal, .dialog');

window.addEventListener('pointermove', (e) => {
  state.inside = true;
  state.ndc.set((e.clientX / window.innerWidth) * 2 - 1, -(e.clientY / window.innerHeight) * 2 + 1);
  state.lastMove = time;
  hud.moveReticle(e.clientX, e.clientY, e.pointerType);
  if (state.dragging) {
    const dx = e.clientX - state.last.x;
    const dy = e.clientY - state.last.y;
    state.rot.y += dx * 0.006;
    state.rot.x += dy * 0.006;
    state.vel.lerp(new THREE.Vector2(dy * 0.006, dx * 0.006), 0.5);
    state.moved += Math.abs(dx) + Math.abs(dy);
  }
  state.last.set(e.clientX, e.clientY);
});

window.addEventListener('pointerdown', (e) => {
  audio.kick();
  if (isUI(e.target)) return;
  state.dragging = true;
  state.moved = 0;
  state.vel.set(0, 0);
  state.last.set(e.clientX, e.clientY);
  hud.moveReticle(e.clientX, e.clientY, e.pointerType);
});

window.addEventListener('pointerup', () => {
  if (state.dragging && state.moved < 6) {
    holo.shock(1); // click -> pulse
    state.energy += 0.8;
    state.glitch = 1;
  }
  state.dragging = false;
});

document.documentElement.addEventListener('pointerleave', () => {
  state.inside = false;
});

window.addEventListener(
  'wheel',
  (e) => {
    state.zoomTarget = THREE.MathUtils.clamp(state.zoomTarget * Math.exp(e.deltaY * 0.001), 0.6, 1.7);
  },
  { passive: true },
);

// ------------------------------------------------------------ JARVIS link
const askForm = document.getElementById('ask');
const askInput = document.getElementById('ask-input');
const talkBtn = document.getElementById('btn-talk');
const approvalEl = document.getElementById('approval');
let approvalId = null;
let jarvisState = 'idle';
let greeted = false;
let booted = false;

// Spoken welcome: only after the boot click (browsers block audio before a
// user gesture) and once the backend link is up.
function maybeGreet() {
  if (booted && !greeted && link.connected) greeted = link.send({ type: 'greet' });
}

function readyStatus() {
  if (!link.connected) return;
  if (audio.speaking) hud.setStatus('SPEAKING', 'live');
  else if (jarvisState === 'idle') hud.setStatus(audio.micReady ? 'READY · HOLD SPACE' : 'READY · TEXT', 'idle');
}

const link = new JarvisLink({
  open() {
    hud.setLink(true);
    hud.log('UPLINK ESTABLISHED', 'hot');
    readyStatus();
    maybeGreet();
  },
  close(reason) {
    hud.setLink(false, 'OFFLINE');
    hud.setStatus(reason, 'error');
    jarvisState = 'idle';
    state.busy = false;
  },
  message: onMessage,
});

function onMessage(msg) {
  switch (msg.type) {
    case 'hello':
      hud.setSystem(msg.status);
      (msg.status.notes || []).forEach((note) => hud.log(note.toUpperCase(), 'warn'));
      break;
    case 'status':
      hud.setSystem(msg.status);
      break;
    case 'state':
      jarvisState = msg.state;
      state.busy = msg.state === 'thinking' || msg.state === 'transcribing';
      if (msg.state === 'thinking') hud.setStatus('THINKING', 'wait');
      else if (msg.state === 'transcribing') hud.setStatus('TRANSCRIBING', 'wait');
      else readyStatus();
      break;
    case 'transcript':
      hud.say('user', msg.text);
      hud.log(`HEARD: ${msg.text}`);
      break;
    case 'tool':
      hud.log(`TOOL ${msg.tool.toUpperCase()} → ${msg.status.toUpperCase()}`, msg.ok ? 'hot' : 'warn');
      holo.shock(0.5);
      break;
    case 'reply':
      if (msg.greeting) hud.say('user', '');
      hud.say('jarvis', msg.text);
      hud.say('actions', msg.summary || '');
      if (!msg.ok) hud.log(`ERROR: ${msg.text}`.toUpperCase(), 'warn');
      holo.shock(0.9);
      state.glitch = Math.max(state.glitch, 0.4);
      link.send({ type: 'status' });
      break;
    case 'speak':
      audio
        .playSpeech(msg.url)
        .then(() => hud.setStatus('SPEAKING', 'live'))
        .catch(() => hud.log('CLICK THE PAGE TO ALLOW AUDIO PLAYBACK', 'warn'));
      break;
    case 'approval':
      showApproval(msg);
      break;
    case 'notice':
      hud.log(msg.text.toUpperCase(), msg.level === 'info' ? '' : msg.level === 'reminder' ? 'hot' : 'warn');
      if (msg.level === 'reminder') {
        hud.say('actions', msg.text);
        holo.shock(1.4);
        state.glitch = 1;
      }
      break;
    default:
  }
}
audio.onSpeechEnd = readyStatus;

// typed input
askForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = askInput.value.trim();
  if (!text) return;
  audio.stopSpeech();
  if (!link.send({ type: 'text', text })) {
    hud.log('BACKEND OFFLINE — MESSAGE NOT SENT', 'warn');
    return;
  }
  hud.say('user', text);
  hud.say('actions', '');
  askInput.value = '';
});

// push-to-talk
let talking = false;
async function startTalk() {
  if (talking || approvalId) return;
  if (!audio.micReady) {
    hud.log('MICROPHONE NOT ACTIVE — RELOAD AND ACTIVATE IT', 'warn');
    return;
  }
  if (!link.connected) {
    hud.log('BACKEND OFFLINE', 'warn');
    return;
  }
  audio.stopSpeech();
  if (!audio.startRecording()) return;
  talking = true;
  talkBtn.classList.add('active');
  hud.setStatus('LISTENING', 'live');
  holo.shock(0.6);
}

async function endTalk() {
  if (!talking) return;
  talking = false;
  talkBtn.classList.remove('active');
  const clip = await audio.stopRecording();
  if (!clip) return readyStatus();
  if (clip.duration < 0.35) {
    hud.log('TOO SHORT — HOLD WHILE SPEAKING', 'warn');
    return readyStatus();
  }
  if (clip.peak < 0.015) {
    hud.log('NO SPEECH DETECTED — CHECK MIC LEVEL', 'warn');
    return readyStatus();
  }
  hud.setStatus('UPLOADING', 'wait');
  if (!(await link.sendAudio(clip.blob, clip.mime))) {
    hud.log('BACKEND OFFLINE — AUDIO NOT SENT', 'warn');
    readyStatus();
  }
}

talkBtn.addEventListener('pointerdown', (e) => {
  e.preventDefault();
  talkBtn.setPointerCapture(e.pointerId);
  startTalk();
});
talkBtn.addEventListener('pointerup', endTalk);
talkBtn.addEventListener('pointercancel', endTalk);
document.getElementById('btn-stop').addEventListener('click', () => audio.stopSpeech());

// approvals
function showApproval(msg) {
  approvalId = msg.id;
  document.getElementById('approval-tool').textContent = `TOOL: ${msg.tool.toUpperCase()}`;
  document.getElementById('approval-preview').textContent = msg.preview;
  approvalEl.classList.add('show');
  hud.log(`APPROVAL REQUESTED: ${msg.tool.toUpperCase()}`, 'hot');
  holo.shock(1);
}

function answerApproval(approved) {
  if (!approvalId) return;
  link.send({ type: 'approve', id: approvalId, approved });
  hud.log(approved ? 'ACTION APPROVED' : 'ACTION DENIED', approved ? 'hot' : 'warn');
  approvalId = null;
  approvalEl.classList.remove('show');
}
document.getElementById('approval-yes').addEventListener('click', () => answerApproval(true));
document.getElementById('approval-no').addEventListener('click', () => answerApproval(false));

// keep reminder count etc. fresh
setInterval(() => link.send({ type: 'status' }), 15000);

// ----------------------------------------------------------------- boot
const boot = document.getElementById('boot');

function finishBoot() {
  if (booted) return;
  booted = true;
  boot.classList.add('gone');
  document.body.classList.remove('booting');
  holo.shock(1.4);
  state.glitch = 1;
  hud.log('INTERFACE ONLINE', 'hot');
  readyStatus();
  maybeGreet();
}

async function selectSource(src) {
  audio.prime(); // unlock audio playback inside this click
  if (src === 'mic') {
    try {
      hud.setStatus('REQUESTING MIC', 'wait');
      await audio.useMic();
      hud.log('MICROPHONE LINK ESTABLISHED');
    } catch (err) {
      console.warn(err);
      hud.setStatus('MIC UNAVAILABLE', 'error');
      hud.log(`MIC ERROR: ${err.name === 'Error' ? err.message : err.name}`.toUpperCase(), 'warn');
    }
  } else {
    hud.log('TEXT MODE — TYPE BELOW');
  }
  finishBoot();
  if (!audio.micReady) askInput.focus();
}

document.querySelectorAll('#boot [data-src]').forEach((btn) => {
  btn.addEventListener('click', () => selectSource(btn.dataset.src));
});

// keyboard
window.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const key = e.key.toLowerCase();

  if (approvalId) {
    if (key === 'y') answerApproval(true);
    else if (key === 'n' || key === 'escape') answerApproval(false);
    e.preventDefault();
    return;
  }
  if (document.activeElement === askInput) {
    if (key === 'escape') askInput.blur();
    return;
  }
  if (!booted) return;
  if (key === ' ') {
    e.preventDefault();
    if (!e.repeat) startTalk();
    return;
  }
  if (e.repeat) return;
  switch (key) {
    case 'escape':
      audio.stopSpeech();
      break;
    case '/':
    case 't':
      e.preventDefault();
      askInput.focus();
      break;
    case 'h':
      hud.toggle();
      break;
    case 'f':
      if (document.fullscreenElement) document.exitFullscreen();
      else document.documentElement.requestFullscreen?.();
      break;
    case '[':
      audio.sensitivity = Math.max(0.25, audio.sensitivity / 1.2);
      hud.log(`INPUT GAIN ${audio.sensitivity.toFixed(2)}x`);
      break;
    case ']':
      audio.sensitivity = Math.min(6, audio.sensitivity * 1.2);
      hud.log(`INPUT GAIN ${audio.sensitivity.toFixed(2)}x`);
      break;
    default:
  }
});

window.addEventListener('keyup', (e) => {
  if (e.key === ' ' && talking) {
    e.preventDefault();
    endTalk();
  }
});
window.addEventListener('blur', endTalk);

// boot text
const bootLog = document.getElementById('boot-log');
[
  'JARVIS CORE 3.0 .................... OK',
  'HOLOGRAPHIC MATRIX ................. ONLINE',
  'SPECTRAL ANALYZER  2048-PT FFT ..... READY',
  'BACKEND UPLINK  127.0.0.1 .......... PENDING',
  'AWAITING OPERATOR_',
].forEach((line, i) => {
  setTimeout(() => {
    bootLog.textContent += (i ? '\n' : '') + line;
  }, 300 + i * 340);
});

link.connect();

// ------------------------------------------------------------------ resize
function resize() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  camera.aspect = w / h;
  // keep the whole instrument cluster in frame on tall/narrow screens
  state.baseDist = Math.max(5.2, 5.08 / camera.aspect);
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
  post.setSize(w, h);
}
window.addEventListener('resize', resize);
resize();

// -------------------------------------------------------------------- loop
let prev = performance.now();
renderer.setAnimationLoop(() => {
  const now = performance.now();
  const dt = Math.min((now - prev) / 1000, 0.05);
  prev = now;
  time += dt;

  audio.update(dt, time);
  spectrumTex.needsUpdate = true;

  // pointer smoothing + drag inertia
  state.smooth.lerp(state.ndc, 1 - Math.exp(-dt * 4));
  if (state.dragging) {
    state.vel.multiplyScalar(Math.pow(0.0005, dt));
  } else {
    state.rot.x += state.vel.x * dt * 60;
    state.rot.y += state.vel.y * dt * 60;
    state.vel.multiplyScalar(Math.pow(0.03, dt));
  }
  state.rot.x = THREE.MathUtils.clamp(state.rot.x, -1.2, 1.2);

  // camera parallax + zoom
  state.zoom += (state.zoomTarget - state.zoom) * (1 - Math.exp(-dt * 6));
  camera.position.set(state.smooth.x * 0.35, 0.2 + state.smooth.y * 0.2, state.baseDist * state.zoom);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();

  // cursor ray → particle repulsion + hover lock
  raycaster.setFromCamera(state.ndc, camera);
  holo.u.uRayO.value.copy(raycaster.ray.origin);
  holo.u.uRayD.value.copy(raycaster.ray.direction);
  state.hovered = state.inside && raycaster.ray.intersectSphere(hitSphere, state.hit) !== null;
  const active = state.inside && time - state.lastMove < 2.5 ? 1 : 0;
  state.mouseForce += (active - state.mouseForce) * (1 - Math.exp(-dt * 3));
  holo.u.uMouseForce.value = state.mouseForce;

  // energy: hover, listening and thinking all spin the construct up
  const target = (state.hovered ? 0.4 : 0) + (talking ? 0.5 : 0) + (state.busy ? 0.35 + 0.15 * Math.sin(time * 6) : 0);
  state.energy += (target - state.energy) * (1 - Math.exp(-dt * 2.5));
  if (audio.beat) {
    holo.shock(0.35 + audio.bass * 0.3);
    state.glitch = Math.max(state.glitch, 0.45);
    hud.onBeat();
  }
  state.glitch *= Math.exp(-dt * 6);

  // orientation: drag + lean toward the cursor
  holo.root.rotation.set(state.rot.x - state.smooth.y * 0.35, state.rot.y + state.smooth.x * 0.55, 0);
  holo.face.rotation.set(-state.smooth.y * 0.2, state.smooth.x * 0.28, 0);

  holo.update(dt, time, audio, state);
  post.update(time, audio, state, holo.pulse);
  post.render();
  hud.update(dt, time, audio, state, camera, holo);
});
