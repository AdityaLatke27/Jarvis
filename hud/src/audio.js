// Audio input + analysis for the JARVIS HUD.
// Sources: the microphone (always analyzed once enabled, never played back)
// and JARVIS's own voice replies, which drive the hologram while speaking.
// Produces a log-spaced 256-bin spectrum (0..1), a normalized waveform,
// smoothed bass/mid/treble bands and beat events. With no live source it
// synthesizes a gentle "idle" signal so the hologram keeps breathing.

const SPEC_BINS = 256;

export class AudioEngine {
  constructor() {
    this.ctx = null;
    this.analyser = null;
    this.mode = 'idle'; // idle | mic | speech
    this.micReady = false;
    this.paused = false;
    this.sensitivity = 1;
    this.agc = 1;
    this.peak = 0.3;

    this.freq = new Uint8Array(1024);
    this.time = new Uint8Array(2048);
    this.spectrum = new Float32Array(SPEC_BINS);
    this.specBytes = new Uint8Array(SPEC_BINS);
    this.wave = new Float32Array(512);

    this.bass = 0;
    this.mid = 0;
    this.treble = 0;
    this.level = 0;
    this.rms = 0;
    this.beat = false;
    this._bassAvg = 0;
    this._cool = 0;

    this.recorder = null;
    this.recordPeak = 0;
    this.onSpeechEnd = null;
  }

  get live() {
    return this.mode !== 'idle' && !!this.ctx && this.ctx.state === 'running' && !this.paused;
  }

  get gain() {
    return this.sensitivity * this.agc;
  }

  get recording() {
    return this.recorder?.state === 'recording';
  }

  // ---------------------------------------------------------------- setup
  _ensure() {
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AC();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 2048;
      this.analyser.smoothingTimeConstant = 0.72;
      this.analyser.minDecibels = -92;
      this.analyser.maxDecibels = -22;
      this.freq = new Uint8Array(this.analyser.frequencyBinCount);
      this.time = new Uint8Array(this.analyser.fftSize);
      this._buildMap();

      // JARVIS voice: <audio> -> analyser -> speakers
      this.voice = new Audio();
      this.voiceSource = this.ctx.createMediaElementSource(this.voice);
      this.voiceGain = this.ctx.createGain();
      this.voiceSource.connect(this.voiceGain);
      this.voiceGain.connect(this.ctx.destination);
      this.voice.addEventListener('ended', () => this._endSpeech());
      this.voice.addEventListener('error', () => this._endSpeech());
    }
    if (this.ctx.state === 'suspended') this.ctx.resume();
  }

  _buildMap() {
    const n = this.freq.length;
    const nyq = this.ctx.sampleRate / 2;
    const fMin = 30;
    const fMax = Math.min(16000, nyq);
    this._map = new Uint16Array(SPEC_BINS + 1);
    for (let i = 0; i <= SPEC_BINS; i++) {
      const f = fMin * Math.pow(fMax / fMin, i / SPEC_BINS);
      this._map[i] = Math.min(n - 1, Math.max(1, Math.round((f / nyq) * n)));
    }
  }

  // create/resume the context inside a user gesture
  prime() {
    this._ensure();
  }

  // call from any user gesture to recover from autoplay restrictions
  kick() {
    if (this.ctx?.state === 'suspended' && !this.paused) this.ctx.resume();
  }

  // ------------------------------------------------------------ microphone
  async useMic() {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('Microphone needs https or localhost');
    }
    this._ensure();
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
    });
    this.stream = stream;
    this.micSource = this.ctx.createMediaStreamSource(stream);
    this.micReady = true;
    this._routeMic(true);
    this.mode = 'mic';
    stream.getAudioTracks()[0]?.addEventListener('ended', () => {
      this.micReady = false;
      if (this.mode === 'mic') this.mode = 'idle';
    });
  }

  _routeMic(on) {
    if (!this.micSource) return;
    try {
      this.micSource.disconnect();
    } catch {
      /* not connected */
    }
    if (on) this.micSource.connect(this.analyser); // analyzed only, not routed to speakers
  }

  // ---------------------------------------------------------- push-to-talk
  startRecording() {
    if (!this.micReady || this.recording) return false;
    const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
    const mimeType = types.find((t) => window.MediaRecorder?.isTypeSupported?.(t)) || '';
    this.recorder = new MediaRecorder(this.stream, mimeType ? { mimeType } : undefined);
    this.chunks = [];
    this.recordPeak = 0;
    this.recordStart = performance.now();
    this.recorder.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.recorder.start();
    return true;
  }

  // resolves to { blob, mime, duration, peak } or null
  stopRecording() {
    return new Promise((resolve) => {
      if (!this.recording) return resolve(null);
      this.recorder.onstop = () => {
        const mime = this.recorder.mimeType || 'audio/webm';
        resolve({
          blob: new Blob(this.chunks, { type: mime }),
          mime,
          duration: (performance.now() - this.recordStart) / 1000,
          peak: this.recordPeak,
        });
      };
      this.recorder.stop();
    });
  }

  // --------------------------------------------------------- JARVIS voice
  async playSpeech(url) {
    this._ensure();
    this.stopSpeech();
    this._routeMic(false); // only JARVIS's voice drives the hologram while it talks
    this.voiceSource.connect(this.analyser);
    this.mode = 'speech';
    this.voice.src = url;
    try {
      await this.voice.play();
    } catch (err) {
      this._endSpeech();
      throw err;
    }
  }

  get speaking() {
    return this.mode === 'speech';
  }

  stopSpeech() {
    if (!this.voice || this.mode !== 'speech') return;
    this.voice.pause();
    this._endSpeech();
  }

  _endSpeech() {
    if (this.mode !== 'speech') return;
    try {
      this.voiceSource.disconnect(this.analyser);
    } catch {
      /* not connected */
    }
    this._routeMic(true);
    this.mode = this.micReady ? 'mic' : 'idle';
    this.onSpeechEnd?.();
  }

  togglePause() {
    if (!this.ctx || this.mode === 'idle') return;
    this.paused = !this.paused;
    if (this.paused) this.ctx.suspend();
    else this.ctx.resume();
  }

  // --------------------------------------------------------------- analysis
  update(dt, t) {
    const spec = this.spectrum;

    if (this.live) {
      this.analyser.getByteFrequencyData(this.freq);
      this.analyser.getByteTimeDomainData(this.time);

      // automatic gain for quiet mic input
      if (this.mode === 'mic') {
        let s = 0;
        const n = this.freq.length >> 2;
        for (let i = 0; i < n; i++) s += this.freq[i];
        const raw = s / n / 255;
        this.peak = Math.max(raw, this.peak - dt * 0.04);
        const target = Math.min(3, Math.max(1, 0.42 / Math.max(this.peak, 0.05)));
        this.agc += (target - this.agc) * Math.min(1, dt * 0.8);
      } else {
        this.agc += (1 - this.agc) * Math.min(1, dt * 2);
      }

      const gain = this.gain;
      const m = this._map;
      const f = this.freq;
      for (let i = 0; i < SPEC_BINS; i++) {
        let v = 0;
        const lo = m[i];
        const hi = Math.max(lo, m[i + 1] - 1);
        for (let b = lo; b <= hi; b++) if (f[b] > v) v = f[b];
        v = (v / 255) * (1 + (i / SPEC_BINS) * 0.5) * gain;
        v = Math.min(1, Math.max(0, (v - 0.12) / 0.88));
        v = Math.pow(v, 1.7); // expand contrast so only real peaks hit the top
        spec[i] = v > spec[i] ? v : Math.max(v, spec[i] - dt * 1.6);
      }
      const wg = Math.min(3, gain);
      for (let i = 0; i < 512; i++) this.wave[i] = ((this.time[i * 4] - 128) / 128) * wg;
    } else {
      for (let i = 0; i < SPEC_BINS; i++) {
        const x = i / SPEC_BINS;
        const v =
          0.16 * (1 - x) +
          0.12 * (0.5 + 0.5 * Math.sin(t * 1.7 + x * 18)) * (0.5 + 0.5 * Math.sin(t * 0.63 - x * 7)) +
          0.04 * Math.sin(t * 0.9 + i * 0.37);
        spec[i] += (Math.max(0, v) - spec[i]) * Math.min(1, dt * 4);
      }
      for (let i = 0; i < 512; i++) {
        this.wave[i] = Math.sin(i * 0.05 + t * 2.2) * 0.12 * Math.sin(t * 0.7 + i * 0.011) + Math.sin(i * 0.23 - t * 4) * 0.03;
      }
    }

    for (let i = 0; i < SPEC_BINS; i++) this.specBytes[i] = spec[i] * 255;

    let sq = 0;
    for (let i = 0; i < 512; i++) sq += this.wave[i] * this.wave[i];
    this.rms = Math.sqrt(sq / 512);
    if (this.recording) this.recordPeak = Math.max(this.recordPeak, this.rms);

    // bands: ~30–140 Hz, ~140–2k Hz, ~2k–12k Hz
    const avg = (a, b) => {
      let s = 0;
      for (let i = a; i < b; i++) s += spec[i];
      return s / (b - a);
    };
    const rawBass = avg(0, 64);
    const rawMid = avg(64, 172);
    const rawTreb = avg(172, 245);
    const follow = (cur, v) => cur + (v - cur) * Math.min(1, dt * (v > cur ? 18 : 5));
    this.bass = follow(this.bass, rawBass);
    this.mid = follow(this.mid, rawMid);
    this.treble = follow(this.treble, rawTreb);
    this.level = (this.bass + this.mid + this.treble) / 3;

    // onset detection on the bass band
    this.beat = false;
    this._cool -= dt;
    this._bassAvg += (rawBass - this._bassAvg) * Math.min(1, dt * 2.2);
    if (this.live && rawBass > 0.28 && rawBass > this._bassAvg * 1.18 + 0.04 && this._cool <= 0) {
      this.beat = true;
      this._cool = 0.26;
    }
  }
}
