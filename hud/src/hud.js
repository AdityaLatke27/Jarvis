// 2D overlay: readouts, mini analyzers, event log, conversation and the cursor reticle.

const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const pad = (n) => String(n).padStart(2, '0');
const deg = (r) => (r * 180) / Math.PI;

export class HUD {
  constructor() {
    const $ = (id) => document.getElementById(id);
    this.root = $('hud');
    this.reticle = $('reticle');
    this.ret = { x: $('ret-x'), y: $('ret-y'), s: $('ret-state') };
    this.statusEl = $('status');
    this.statusText = $('status-text');
    this.clock = $('clock');
    this.date = $('date');
    this.r = {
      link: $('r-link'),
      model: $('r-model'),
      memory: $('r-memory'),
      stt: $('r-stt'),
      tts: $('r-tts'),
      tools: $('r-tools'),
      reminders: $('r-reminders'),
      db: $('r-db'),
      gain: $('r-gain'),
      az: $('r-az'),
      fps: $('r-fps'),
    };
    this.m = { bass: $('m-bass'), mid: $('m-mid'), treble: $('m-treble') };
    this.v = { bass: $('v-bass'), mid: $('v-mid'), treble: $('v-treble') };
    this.logEl = $('log');
    this.said = { user: $('said-user'), jarvis: $('said-jarvis'), actions: $('said-actions') };

    this.cells = [];
    const seg = $('seg-level');
    for (let i = 0; i < 40; i++) {
      const c = document.createElement('i');
      seg.appendChild(c);
      this.cells.push(c);
    }
    this.lit = -1;

    this.spec = $('c-spec').getContext('2d');
    this.wave = $('c-wave').getContext('2d');
    this.peaks = new Float32Array(56);

    this.hidden = false;
    this.locked = false;
    this.textT = 0;
    this.frames = 0;
    this.fpsT = 0;
    this.fps = 60;
    this.now = 0;
  }

  // ------------------------------------------------------------------ api
  log(msg, cls = '') {
    const d = new Date();
    const line = document.createElement('div');
    if (cls) line.className = cls;
    line.textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}  ${msg}`;
    line.title = msg;
    this.logEl.appendChild(line);
    while (this.logEl.childElementCount > 13) this.logEl.firstElementChild.remove();
  }

  setStatus(text, kind = 'idle') {
    this.statusText.textContent = text;
    this.statusEl.className = `status ${kind}`;
  }

  setLink(online, text) {
    this.r.link.textContent = text || (online ? 'ONLINE' : 'OFFLINE');
    this.r.link.className = online ? '' : 'bad';
  }

  setSystem(status) {
    const set = (el, text, ok = true) => {
      el.textContent = text;
      el.className = ok ? '' : 'bad';
      el.title = text;
    };
    set(this.r.model, (status.model || '—').toUpperCase());
    set(this.r.memory, status.memory ? 'ONLINE' : 'OFFLINE', status.memory);
    set(this.r.stt, status.voice_input ? 'ELEVENLABS' : 'DISABLED', status.voice_input);
    set(this.r.tts, (status.tts || 'none').toUpperCase(), status.tts !== 'none');
    set(this.r.tools, String(status.tools ?? '—'));
    set(this.r.reminders, String(status.reminders ?? '—'));
  }

  say(who, text) {
    const el = this.said[who];
    el.textContent = text || '';
    el.classList.toggle('show', !!text);
    el.scrollTop = 0;
  }

  toggle() {
    this.hidden = !this.hidden;
    this.root.classList.toggle('hidden', this.hidden);
  }

  onBeat() {
    this.root.classList.add('beat');
    clearTimeout(this._beatTimer);
    this._beatTimer = setTimeout(() => this.root.classList.remove('beat'), 110);
  }

  moveReticle(x, y, pointerType) {
    this.reticle.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    this.reticle.classList.toggle('touch', pointerType === 'touch');
    this.ret.x.textContent = `X ${((x / window.innerWidth) * 2 - 1).toFixed(3)}`;
    this.ret.y.textContent = `Y ${(-(y / window.innerHeight) * 2 + 1).toFixed(3)}`;
  }

  _setLock(on) {
    if (on === this.locked) return;
    this.locked = on;
    this.reticle.classList.toggle('lock', on);
    this.ret.s.textContent = on ? 'LOCK' : 'SCAN';
  }

  // --------------------------------------------------------------- drawing
  _drawSpec(spec) {
    const g = this.spec;
    const W = g.canvas.width;
    const H = g.canvas.height;
    const N = this.peaks.length;
    g.clearRect(0, 0, W, H);
    g.fillStyle = 'rgba(255,170,70,0.10)';
    for (let y = 1; y < 4; y++) g.fillRect(0, Math.round((y * H) / 4), W, 1);
    const bw = W / N;
    for (let i = 0; i < N; i++) {
      const a = Math.floor((i / N) * 236);
      const b = Math.max(a + 1, Math.floor(((i + 1) / N) * 236));
      let v = 0;
      for (let k = a; k < b; k++) v = Math.max(v, spec[k]);
      this.peaks[i] = Math.max(v, this.peaks[i] - 0.012);
      const h = Math.max(2, v * (H - 8));
      g.fillStyle = `rgba(255,${Math.round(140 + v * 100)},${Math.round(40 + v * 110)},${0.35 + v * 0.65})`;
      g.fillRect(i * bw + 1, H - h, bw - 2, h);
      g.fillStyle = 'rgba(255,230,180,0.9)';
      g.fillRect(i * bw + 1, H - 4 - this.peaks[i] * (H - 8), bw - 2, 2);
    }
  }

  _drawWave(wave) {
    const g = this.wave;
    const W = g.canvas.width;
    const H = g.canvas.height;
    g.clearRect(0, 0, W, H);
    g.fillStyle = 'rgba(255,170,70,0.12)';
    g.fillRect(0, H / 2, W, 1);
    g.strokeStyle = 'rgba(255,205,130,0.95)';
    g.lineWidth = 2;
    g.shadowColor = 'rgba(255,140,30,0.9)';
    g.shadowBlur = 8;
    g.beginPath();
    const n = wave.length;
    for (let i = 0; i < n; i += 2) {
      const x = (i / (n - 1)) * W;
      const y = H / 2 - Math.max(-1, Math.min(1, wave[i])) * H * 0.42;
      if (i) g.lineTo(x, y);
      else g.moveTo(x, y);
    }
    g.stroke();
    g.shadowBlur = 0;
  }

  // ---------------------------------------------------------------- update
  update(dt, t, audio, state, camera, holo) {
    this.now = t;
    this.frames++;
    this.fpsT += dt;
    if (this.fpsT >= 0.5) {
      this.fps = this.frames / this.fpsT;
      this.frames = 0;
      this.fpsT = 0;
    }
    this._setLock(state.hovered);
    if (this.hidden) return;

    this._drawSpec(audio.spectrum);
    this._drawWave(audio.wave);

    const lit = Math.round(Math.min(1, audio.level * 1.7) * this.cells.length);
    if (lit !== this.lit) {
      this.lit = lit;
      const hot = this.cells.length * 0.8;
      this.cells.forEach((c, i) => {
        c.className = i < lit ? (i >= hot ? 'on hot' : 'on') : '';
      });
    }
    for (const k of ['bass', 'mid', 'treble']) {
      this.m[k].style.transform = `scaleX(${Math.min(1, audio[k]).toFixed(3)})`;
    }

    this.textT -= dt;
    if (this.textT <= 0) {
      this.textT = 0.1;
      const d = new Date();
      this.clock.textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
      this.date.textContent = `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())} · ${DAYS[d.getDay()]}`;
      this.r.db.textContent = audio.rms > 0.0005 ? `${(20 * Math.log10(audio.rms)).toFixed(1)} dB` : '-∞ dB';
      this.r.gain.textContent = `${audio.gain.toFixed(2)}x`;
      this.r.fps.textContent = this.fps.toFixed(0);
      const az = (((deg(holo.root.rotation.y + holo.spin.rotation.y) % 360) + 360) % 360).toFixed(1);
      this.r.az.textContent = `${az.padStart(5, '0')}°`;
      for (const k of ['bass', 'mid', 'treble']) this.v[k].textContent = Math.round(audio[k] * 100);
    }
  }
}
