import * as THREE from 'three';
import * as S from './shaders.js';
import { makeGlowTexture, makeGlyphAtlas } from './textures.js';

// Deterministic RNG so the construct has the same layout on every load.
function mulberry32(seed) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rng = mulberry32(0x9e3779b1);
const rand = (a = 0, b = 1) => a + rng() * (b - a);
const sign = () => (rng() < 0.5 ? -1 : 1);
const pick = (arr) => arr[Math.floor(rng() * arr.length)];
const gauss = () => Math.sqrt(-2 * Math.log(1 - rng())) * Math.cos(2 * Math.PI * rng());
const randDir = (out = new THREE.Vector3()) => {
  const z = rand(-1, 1);
  const a = rand(0, Math.PI * 2);
  const s = Math.sqrt(1 - z * z);
  return out.set(s * Math.cos(a), z, s * Math.sin(a));
};
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const easeOutCubic = (x) => 1 - Math.pow(1 - x, 3);
const easeOutBack = (x) => 1 + 2.4 * Math.pow(x - 1, 3) + 1.4 * Math.pow(x - 1, 2);

const TAU = Math.PI * 2;
const Z = new THREE.Vector3(0, 0, 1);

export const COLORS = {
  deep: new THREE.Color('#ff5212'),
  amber: new THREE.Color('#ff9326'),
  gold: new THREE.Color('#ffbf4d'),
  pale: new THREE.Color('#ffd98c'),
  hot: new THREE.Color('#fff2d0'),
  steel: new THREE.Color('#2f6fb8'),
};

// --------------------------------------------------------------------------
// geometry helpers — everything lives in the local XY plane and carries an
// `aT` attribute (0..1 along the element) for the line shader.
// --------------------------------------------------------------------------
function arcGeo(r, start, len, segsPerCircle = 160) {
  const n = Math.max(6, Math.ceil((segsPerCircle * len) / TAU));
  const pos = new Float32Array((n + 1) * 3);
  const t = new Float32Array(n + 1);
  for (let i = 0; i <= n; i++) {
    const a = start + (len * i) / n;
    pos[i * 3] = Math.cos(a) * r;
    pos[i * 3 + 1] = Math.sin(a) * r;
    t[i] = i / n;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('aT', new THREE.BufferAttribute(t, 1));
  return g;
}

function withT(geo) {
  const n = geo.attributes.position.count;
  const t = new Float32Array(n);
  for (let i = 0; i < n; i++) t[i] = i / n;
  geo.setAttribute('aT', new THREE.BufferAttribute(t, 1));
  return geo;
}

class Geo {
  constructor() {
    this.pos = [];
    this.t = [];
    this.idx = [];
  }

  // arc as line-segment pairs
  arc(r, a0, len, n, t0 = 0, t1 = 1) {
    for (let i = 0; i < n; i++) {
      const b0 = a0 + (len * i) / n;
      const b1 = a0 + (len * (i + 1)) / n;
      this.pos.push(Math.cos(b0) * r, Math.sin(b0) * r, 0, Math.cos(b1) * r, Math.sin(b1) * r, 0);
      this.t.push(t0 + ((t1 - t0) * i) / n, t0 + ((t1 - t0) * (i + 1)) / n);
    }
    return this;
  }

  dashed(r, a0, span, dashes, duty) {
    for (let i = 0; i < dashes; i++) {
      const s0 = a0 + (span * i) / dashes;
      this.arc(r, s0, (span / dashes) * duty, 3, i / dashes, (i + duty) / dashes);
    }
    return this;
  }

  ticks(r, a0, span, count, len, majorEvery = 10, majorLen = len * 2.2) {
    for (let i = 0; i < count; i++) {
      const a = a0 + (span * i) / count;
      const L = i % majorEvery === 0 ? majorLen : len;
      const c = Math.cos(a);
      const s = Math.sin(a);
      this.pos.push(c * r, s * r, 0, c * (r + L), s * (r + L), 0);
      this.t.push(i / count, i / count);
    }
    return this;
  }

  // filled annulus segment (indexed triangles)
  band(r, w, a0, a1, t0 = 0, t1 = 1, segs = 8) {
    const base = this.pos.length / 3;
    for (let i = 0; i <= segs; i++) {
      const f = i / segs;
      const a = a0 + (a1 - a0) * f;
      const c = Math.cos(a);
      const s = Math.sin(a);
      this.pos.push(c * r, s * r, 0, c * (r + w), s * (r + w), 0);
      const tt = t0 + (t1 - t0) * f;
      this.t.push(tt, tt);
    }
    for (let i = 0; i < segs; i++) {
      const k = base + i * 2;
      this.idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2);
    }
    return this;
  }

  build() {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(this.pos, 3));
    g.setAttribute('aT', new THREE.Float32BufferAttribute(this.t, 1));
    if (this.idx.length) g.setIndex(this.idx);
    return g;
  }
}

// --------------------------------------------------------------------------
// The hologram
// --------------------------------------------------------------------------
export class Hologram {
  constructor(spectrumTexture) {
    this.group = new THREE.Group();
    this.root = new THREE.Group(); // tilts toward the cursor / drag
    this.spin = new THREE.Group(); // slow auto-rotation of the shell
    this.inner = new THREE.Group(); // counter-rotating core structures
    this.face = new THREE.Group(); // screen-aligned instrument rings
    this.env = new THREE.Group(); // room: floor + dust
    this.root.add(this.spin, this.inner);
    this.group.add(this.env, this.root, this.face);

    this.u = {
      uTime: { value: 0 },
      uBass: { value: 0 },
      uMid: { value: 0 },
      uTreble: { value: 0 },
      uEnergy: { value: 0 },
      uPulse: { value: 0 },
      uIntro: { value: 0 },
      uSpectrum: { value: spectrumTexture },
      uRayO: { value: new THREE.Vector3(0, 0, 10) },
      uRayD: { value: new THREE.Vector3(0, 0, -1) },
      uMouseForce: { value: 0 },
      uPixelRatio: { value: 1 },
    };

    this.glowTex = makeGlowTexture();
    this.spinners = [];
    this.introT = 0;
    this.pulse = 0;
    this._tmp = new THREE.Vector3();

    this._buildCore();
    this._buildParticles();
    this._buildGridSphere();
    this._buildRings();
    this._buildSpikes();
    this._buildFilaments();
    this._buildPanels();
    this._buildLattice();
    this._buildFace();
    this._buildShockwaves();
    this._buildMarker();
    this._buildEnvironment();
  }

  // ---------------------------------------------------------------- helpers
  _lineMat({ color = COLORS.amber, opacity = 0.6, fade = 0, bin = rng() * 0.8, head = 0, flicker = 0 } = {}) {
    return new THREE.ShaderMaterial({
      uniforms: {
        uTime: this.u.uTime,
        uEnergy: this.u.uEnergy,
        uSpectrum: this.u.uSpectrum,
        uColor: { value: color.clone() },
        uOpacity: { value: opacity },
        uFade: { value: fade },
        uBin: { value: bin },
        uHeadSpeed: { value: head },
        uHeadOffset: { value: rng() },
        uFlicker: { value: flicker },
      },
      vertexShader: S.lineVert,
      fragmentShader: S.lineFrag,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    });
  }

  _sprite(color, opacity, scale) {
    const s = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: this.glowTex,
        color,
        opacity,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    );
    s.scale.setScalar(scale);
    return s;
  }

  // An element that spins in its own plane and scales in during the intro.
  _spinner(obj, parent, { q = null, speed = 0, tumble = 0, delay = 0 } = {}) {
    const holder = new THREE.Group();
    if (q) holder.quaternion.copy(q);
    holder.add(obj);
    parent.add(holder);
    obj.scale.setScalar(0.0001);
    this.spinners.push({ obj, holder, speed, tumble, delay, mat: obj.material, base: obj.material.uniforms.uOpacity.value });
    return holder;
  }

  // Bias ring planes toward equatorial / meridian bands like a gyroscope.
  _orientation() {
    const k = rng();
    const n = new THREE.Vector3();
    if (k < 0.38) n.set(gauss() * 0.28, 1, gauss() * 0.28);
    else if (k < 0.7) {
      const a = rand(0, TAU);
      n.set(Math.cos(a), gauss() * 0.22, Math.sin(a));
    } else randDir(n);
    return new THREE.Quaternion().setFromUnitVectors(Z, n.normalize());
  }

  // ------------------------------------------------------------------ parts
  _buildCore() {
    this.coreBloom = this._sprite(COLORS.deep, 0.06, 6);
    this.coreHalo = this._sprite(COLORS.amber, 0.3, 2.2);
    this.coreHot = this._sprite(COLORS.hot, 1, 0.45);
    this.root.add(this.coreBloom, this.coreHalo, this.coreHot);
  }

  _buildParticles() {
    const N = 16000;
    const pos = new Float32Array(N * 3);
    const rnd = new Float32Array(N * 4);
    const v = new THREE.Vector3();
    const bands = Array.from({ length: 8 }, () => ({
      q: new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), randDir()),
      r: rand(0.86, 1.12),
      w: rand(0.015, 0.06),
    }));

    for (let i = 0; i < N; i++) {
      const k = rng();
      if (k < 0.32) {
        randDir(v).multiplyScalar(1 + gauss() * 0.035); // shell
      } else if (k < 0.84) {
        const b = bands[Math.floor(rng() * bands.length)]; // swirling bands
        const a = rand(0, TAU);
        v.set(Math.cos(a) * b.r, gauss() * b.w, Math.sin(a) * b.r).applyQuaternion(b.q);
      } else if (k < 0.92) {
        randDir(v).multiplyScalar(Math.pow(rng(), 1.8) * 0.9); // interior
      } else {
        randDir(v).multiplyScalar(rand(1.12, 1.55)); // halo sparks
      }
      pos[i * 3] = v.x;
      pos[i * 3 + 1] = v.y;
      pos[i * 3 + 2] = v.z;
      // x = spectrum bin (latitude-weighted so regions pulse together)
      const lat = Math.abs(v.y / Math.max(v.length(), 1e-4));
      rnd[i * 4] = clamp01(lat * 0.7 + rng() * 0.3) * 0.9 + 0.02;
      rnd[i * 4 + 1] = rng();
      rnd[i * 4 + 2] = rng();
      rnd[i * 4 + 3] = rng();
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('aRand', new THREE.BufferAttribute(rnd, 4));

    const u = this.u;
    this.points = new THREE.Points(
      geo,
      new THREE.ShaderMaterial({
        uniforms: {
          uTime: u.uTime,
          uBass: u.uBass,
          uTreble: u.uTreble,
          uEnergy: u.uEnergy,
          uPulse: u.uPulse,
          uIntro: u.uIntro,
          uPixelRatio: u.uPixelRatio,
          uMouseForce: u.uMouseForce,
          uSpectrum: u.uSpectrum,
          uRayO: u.uRayO,
          uRayD: u.uRayD,
          uSize: { value: 24 },
          uColA: { value: COLORS.amber.clone() },
          uColB: { value: COLORS.pale.clone() },
        },
        vertexShader: S.pointsVert,
        fragmentShader: S.pointsFrag,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    );
    this.points.frustumCulled = false;
    this.spin.add(this.points);
  }

  _buildGridSphere() {
    const pos = [];
    const t = [];
    const R = 0.985;
    const n = 120;
    const push = (fn) => {
      for (let i = 0; i < n; i++) {
        pos.push(...fn(i / n), ...fn((i + 1) / n));
        t.push(i / n, (i + 1) / n);
      }
    };
    for (let lat = -75; lat <= 75; lat += 15) {
      const phi = (lat * Math.PI) / 180;
      const y = Math.sin(phi) * R;
      const rr = Math.cos(phi) * R;
      push((f) => [Math.cos(f * TAU) * rr, y, Math.sin(f * TAU) * rr]);
    }
    for (let lon = 0; lon < 180; lon += 15) {
      const th = (lon * Math.PI) / 180;
      push((f) => {
        const a = f * TAU;
        return [Math.cos(a) * Math.cos(th) * R, Math.sin(a) * R, Math.cos(a) * Math.sin(th) * R];
      });
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute('aT', new THREE.Float32BufferAttribute(t, 1));
    const grid = new THREE.LineSegments(geo, this._lineMat({ color: COLORS.amber, opacity: 0.08, bin: 0.05, head: 0.07 }));
    this._spinner(grid, this.spin, { delay: 0.2 });
  }

  _buildRings() {
    const colors = [COLORS.amber, COLORS.gold, COLORS.gold, COLORS.pale, COLORS.deep];
    const radius = () => {
      const k = rng();
      if (k < 0.68) return rand(0.9, 1.18);
      if (k < 0.88) return rand(0.38, 0.86);
      return rand(1.2, 1.42);
    };
    const delayFor = (r) => 0.25 + r * 0.8 + rng() * 0.35;
    const tumble = () => (rng() < 0.25 ? rand(0.05, 0.25) * sign() : 0);

    // thin arcs (some with a twin running alongside)
    for (let i = 0; i < 38; i++) {
      const r = radius();
      const full = rng() < 0.28;
      const start = rand(0, TAU);
      const len = full ? TAU : rand(0.35, 1.75) * Math.PI;
      const mat = this._lineMat({
        color: pick(colors),
        opacity: rand(0.3, 0.85),
        fade: full ? 0 : rng() < 0.6 ? 1 : 0,
        head: rng() < 0.55 ? rand(0.12, 0.55) * sign() : 0,
        flicker: rng() < 0.2 ? 0.5 : 0,
      });
      const line = new THREE.Line(arcGeo(r, start, len), mat);
      if (rng() < 0.3) line.add(new THREE.Line(arcGeo(r + rand(0.01, 0.022), start, len * rand(0.6, 1)), mat));
      this._spinner(line, this.spin, { q: this._orientation(), speed: rand(0.08, 0.8) * sign(), tumble: tumble(), delay: delayFor(r) });
    }

    // dashed arcs
    for (let i = 0; i < 14; i++) {
      const r = rand(0.95, 1.32);
      const span = rng() < 0.4 ? TAU : rand(0.5, 1.8) * Math.PI;
      const dashes = Math.floor((rand(18, 90) * span) / TAU) + 6;
      const g = new Geo().dashed(r, rand(0, TAU), span, dashes, rand(0.25, 0.7)).build();
      const mat = this._lineMat({ color: pick(colors), opacity: rand(0.35, 0.8), head: rng() < 0.4 ? rand(0.1, 0.4) : 0 });
      this._spinner(new THREE.LineSegments(g, mat), this.spin, { q: this._orientation(), speed: rand(0.1, 0.6) * sign(), tumble: tumble(), delay: delayFor(r) });
    }

    // graduated dials
    for (let i = 0; i < 7; i++) {
      const r = rand(0.98, 1.28);
      const span = rng() < 0.5 ? TAU : rand(0.4, 1.2) * Math.PI;
      const count = Math.floor((rand(60, 160) * span) / TAU) + 8;
      const g = new Geo().ticks(r, rand(0, TAU), span, count, rand(0.018, 0.035), Math.floor(rand(4, 10))).build();
      const mat = this._lineMat({ color: pick([COLORS.gold, COLORS.pale]), opacity: rand(0.4, 0.8) });
      this._spinner(new THREE.LineSegments(g, mat), this.spin, { q: this._orientation(), speed: rand(0.05, 0.4) * sign(), delay: delayFor(r) });
    }

    // thick bands and segmented "data rings"
    for (let i = 0; i < 12; i++) {
      const r = rand(0.92, 1.24);
      const a0 = rand(0, TAU);
      const g = new Geo();
      if (rng() < 0.5) {
        g.band(r, rand(0.008, 0.028), a0, a0 + rand(0.12, 0.7) * Math.PI, 0, 1, 24);
      } else {
        const span = rand(0.3, 1.2) * Math.PI;
        const n = Math.floor(rand(10, 48));
        const w = rand(0.012, 0.03);
        const duty = rand(0.45, 0.8);
        for (let k = 0; k < n; k++) {
          const b0 = a0 + (span * k) / n;
          g.band(r, w, b0, b0 + (span / n) * duty, k / n, (k + duty) / n, 2);
        }
      }
      const mat = this._lineMat({
        color: pick(colors),
        opacity: rand(0.25, 0.6),
        fade: rng() < 0.5 ? 1 : 0,
        head: rng() < 0.4 ? rand(0.15, 0.5) * sign() : 0,
      });
      this._spinner(new THREE.Mesh(g.build(), mat), this.spin, { q: this._orientation(), speed: rand(0.1, 0.7) * sign(), tumble: tumble(), delay: delayFor(r) });
    }

    // fast gyroscopic rings around the core
    for (let i = 0; i < 9; i++) {
      const r = rand(0.1, 0.34);
      const len = rng() < 0.5 ? TAU : rand(0.8, 1.7) * Math.PI;
      const mat = this._lineMat({ color: pick([COLORS.hot, COLORS.pale, COLORS.gold]), opacity: rand(0.4, 0.9), head: rand(0.3, 1.0) });
      const q = new THREE.Quaternion().setFromUnitVectors(Z, randDir());
      this._spinner(new THREE.Line(arcGeo(r, rand(0, TAU), len), mat), this.inner, {
        q,
        speed: rand(0.8, 2.6) * sign(),
        tumble: rand(0.2, 0.8) * sign(),
        delay: 0.1 + rng() * 0.4,
      });
    }
  }

  _buildSpikes() {
    const N = 1100;
    const pos = new Float32Array(N * 6);
    const r0 = new Float32Array(N * 2);
    const len = new Float32Array(N * 2);
    const end = new Float32Array(N * 2);
    const rnd = new Float32Array(N * 4);
    const d = new THREE.Vector3();
    for (let i = 0; i < N; i++) {
      randDir(d);
      const base = rand(0.97, 1.08);
      const l = rng() < 0.85 ? rand(0.02, 0.09) : rand(0.12, 0.3);
      const bin = clamp01(Math.abs(d.y) * 0.6 + rng() * 0.4) * 0.85 + 0.02;
      const seed = rng();
      for (let e = 0; e < 2; e++) {
        const k = i * 2 + e;
        pos.set([d.x, d.y, d.z], k * 3);
        r0[k] = base;
        len[k] = l;
        end[k] = e;
        rnd[k * 2] = bin;
        rnd[k * 2 + 1] = seed;
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('aR0', new THREE.BufferAttribute(r0, 1));
    geo.setAttribute('aLen', new THREE.BufferAttribute(len, 1));
    geo.setAttribute('aEnd', new THREE.BufferAttribute(end, 1));
    geo.setAttribute('aRand', new THREE.BufferAttribute(rnd, 2));
    const u = this.u;
    this.spikes = new THREE.LineSegments(
      geo,
      new THREE.ShaderMaterial({
        uniforms: { uTime: u.uTime, uIntro: u.uIntro, uPulse: u.uPulse, uSpectrum: u.uSpectrum, uColor: { value: COLORS.gold.clone() } },
        vertexShader: S.spikeVert,
        fragmentShader: S.spikeFrag,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    );
    this.spikes.frustumCulled = false;
    this.spin.add(this.spikes);
  }

  // Curling energy streams spiralling out of the core.
  _buildFilaments() {
    for (let i = 0; i < 28; i++) {
      const q = new THREE.Quaternion().setFromUnitVectors(Z, randDir());
      const a0 = rand(0, TAU);
      const dir = sign();
      const turns = rand(0.6, 1.6);
      const rIn = rand(0.02, 0.15);
      const rOut = rand(0.45, 0.95);
      const pts = [];
      for (let k = 0; k <= 6; k++) {
        const f = k / 6;
        const a = a0 + dir * turns * Math.PI * f;
        const r = rIn + (rOut - rIn) * f;
        pts.push(new THREE.Vector3(Math.cos(a) * r, Math.sin(a) * r, gauss() * 0.08).applyQuaternion(q));
      }
      const geo = withT(new THREE.BufferGeometry().setFromPoints(new THREE.CatmullRomCurve3(pts).getPoints(90)));
      const mat = this._lineMat({
        color: pick([COLORS.gold, COLORS.hot, COLORS.amber]),
        opacity: rand(0.25, 0.6),
        fade: 0.6,
        head: rand(0.3, 0.9),
        flicker: 0.3,
        bin: rand(0, 0.5),
      });
      this._spinner(new THREE.Line(geo, mat), this.inner, { delay: 0.3 + rng() * 0.6 });
    }
  }

  _buildPanels() {
    const { texture, cells } = makeGlyphAtlas(rng);
    const N = 90;
    const pos = [];
    const uv = [];
    const seed = [];
    const idx = [];
    const d = new THREE.Vector3();
    const tu = new THREE.Vector3();
    const tv = new THREE.Vector3();
    const up = new THREE.Vector3();
    const c = new THREE.Vector3();

    for (let i = 0; i < N; i++) {
      randDir(d);
      const r = rand(1.0, 1.16);
      const w = rand(0.07, 0.2);
      const h = w * rand(0.4, 1.0);
      up.set(0, 1, 0);
      if (Math.abs(d.y) > 0.95) up.set(1, 0, 0);
      tu.crossVectors(up, d).normalize();
      tv.crossVectors(d, tu);
      c.copy(d).multiplyScalar(r);

      const cell = Math.floor(rng() * cells * cells);
      const cx = cell % cells;
      const cy = Math.floor(cell / cells);
      const u0 = cx / cells;
      const u1 = (cx + 1) / cells;
      const v1 = 1 - cy / cells;
      const v0 = v1 - (1 / cells) * (h / w);

      const base = pos.length / 3;
      for (const [sx, sy, uu, vv] of [
        [-1, -1, u0, v0],
        [1, -1, u1, v0],
        [-1, 1, u0, v1],
        [1, 1, u1, v1],
      ]) {
        pos.push(
          c.x + (tu.x * sx * w) / 2 + (tv.x * sy * h) / 2,
          c.y + (tu.y * sx * w) / 2 + (tv.y * sy * h) / 2,
          c.z + (tu.z * sx * w) / 2 + (tv.z * sy * h) / 2,
        );
        uv.push(uu, vv);
        seed.push(i / N);
      }
      idx.push(base, base + 1, base + 2, base + 1, base + 3, base + 2);
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geo.setAttribute('aSeed', new THREE.Float32BufferAttribute(seed, 1));
    geo.setIndex(idx);

    const u = this.u;
    this.panels = new THREE.Mesh(
      geo,
      new THREE.ShaderMaterial({
        uniforms: {
          uTime: u.uTime,
          uTreble: u.uTreble,
          uEnergy: u.uEnergy,
          uIntro: u.uIntro,
          uAtlas: { value: texture },
          uColor: { value: COLORS.gold.clone() },
        },
        vertexShader: S.panelVert,
        fragmentShader: S.panelFrag,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        side: THREE.DoubleSide,
      }),
    );
    this.panels.frustumCulled = false;
    this.spin.add(this.panels);
  }

  // Nested wireframe solids inside the sphere.
  _buildLattice() {
    const solids = [
      [new THREE.IcosahedronGeometry(0.2, 1), COLORS.hot, 0.5],
      [new THREE.BoxGeometry(0.42, 0.42, 0.42), COLORS.amber, 0.28],
      [new THREE.OctahedronGeometry(0.5, 0), COLORS.gold, 0.2],
      [new THREE.BoxGeometry(0.64, 0.64, 0.64), COLORS.gold, 0.16],
      [new THREE.IcosahedronGeometry(0.78, 0), COLORS.amber, 0.08],
    ];
    for (const [g, color, opacity] of solids) {
      const edges = withT(new THREE.EdgesGeometry(g));
      const q = new THREE.Quaternion().setFromUnitVectors(Z, randDir());
      this._spinner(new THREE.LineSegments(edges, this._lineMat({ color, opacity, bin: rng() * 0.3, flicker: 0.2, head: rand(0.1, 0.3) })), this.inner, {
        q,
        speed: rand(0.2, 0.6) * sign(),
        tumble: rand(0.1, 0.4) * sign(),
        delay: 0.2 + rng() * 0.5,
      });
    }
  }

  // Screen-aligned instrument cluster: spectrum ring, waveform, dials.
  _buildFace() {
    const u = this.u;

    // circular spectrum, mirrored left/right
    const N = 168;
    const angle = new Float32Array(N * 4);
    const side = new Float32Array(N * 4);
    const end = new Float32Array(N * 4);
    const bin = new Float32Array(N * 4);
    const idx = [];
    for (let i = 0; i < N; i++) {
      const f = i / N;
      const a = Math.PI / 2 - f * TAU;
      const m = f < 0.5 ? f * 2 : (1 - f) * 2;
      for (let v = 0; v < 4; v++) {
        const k = i * 4 + v;
        angle[k] = a;
        side[k] = v % 2 ? 1 : -1;
        end[k] = v < 2 ? 0 : 1;
        bin[k] = 0.015 + m * 0.8;
      }
      const b = i * 4;
      idx.push(b, b + 1, b + 2, b + 1, b + 3, b + 2);
    }
    const bgeo = new THREE.BufferGeometry();
    bgeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * 12), 3));
    bgeo.setAttribute('aAngle', new THREE.BufferAttribute(angle, 1));
    bgeo.setAttribute('aSide', new THREE.BufferAttribute(side, 1));
    bgeo.setAttribute('aEnd', new THREE.BufferAttribute(end, 1));
    bgeo.setAttribute('aBin', new THREE.BufferAttribute(bin, 1));
    bgeo.setIndex(idx);
    this.bars = new THREE.Mesh(
      bgeo,
      new THREE.ShaderMaterial({
        uniforms: {
          uSpectrum: u.uSpectrum,
          uIntro: u.uIntro,
          uR0: { value: 1.36 },
          uLen: { value: 0.34 },
          uHalfW: { value: (Math.PI / N) * 0.5 },
          uColA: { value: COLORS.gold.clone() },
          uColB: { value: COLORS.hot.clone() },
        },
        vertexShader: S.barsVert,
        fragmentShader: S.barsFrag,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        side: THREE.DoubleSide,
      }),
    );
    this.bars.frustumCulled = false;
    this.face.add(this.bars);

    this._spinner(new THREE.Line(arcGeo(1.345, 0, TAU, 256), this._lineMat({ color: COLORS.gold, opacity: 0.4, bin: 0.1 })), this.face, { delay: 0.9 });

    // live waveform loop
    const W = 256;
    this.wavePos = new Float32Array(W * 3);
    const wgeo = new THREE.BufferGeometry();
    wgeo.setAttribute('position', new THREE.BufferAttribute(this.wavePos, 3));
    wgeo.setAttribute('aT', new THREE.BufferAttribute(Float32Array.from({ length: W }, (_, i) => i / W), 1));
    this.waveLine = new THREE.LineLoop(wgeo, this._lineMat({ color: COLORS.hot, opacity: 0.5, bin: 0.3 }));
    this.waveLine.frustumCulled = false;
    this.face.add(this.waveLine);

    // outer compass dial
    const dial = new Geo().ticks(1.72, 0, TAU, 180, 0.022, 10, 0.05).build();
    this._spinner(new THREE.LineSegments(dial, this._lineMat({ color: COLORS.gold, opacity: 0.5, bin: 0.2 })), this.face, { speed: 0.03, delay: 1.2 });

    // partial outer arcs, counter-rotating
    const outer = [
      { r: 1.8, span: 0.55, kind: 'arc', speed: -0.05 },
      { r: 1.8, span: 0.3, kind: 'arc', speed: -0.05, off: Math.PI },
      { r: 1.85, span: 0.8, kind: 'dash', speed: 0.07 },
      { r: 1.9, span: 0.22, kind: 'band', speed: -0.09 },
      { r: 1.9, span: 0.12, kind: 'band', speed: -0.09, off: 2.2 },
      { r: 1.62, span: 1.1, kind: 'dash', speed: 0.12 },
      { r: 1.97, span: 0.45, kind: 'arc', speed: 0.04 },
      { r: 1.66, span: 0.35, kind: 'band', speed: -0.15 },
    ];
    for (const o of outer) {
      const a0 = o.off ?? rand(0, TAU);
      const span = o.span * Math.PI;
      let obj;
      if (o.kind === 'arc') {
        obj = new THREE.Line(arcGeo(o.r, a0, span, 256), this._lineMat({ color: COLORS.gold, opacity: 0.55, fade: 1, head: 0.2 }));
      } else if (o.kind === 'dash') {
        obj = new THREE.LineSegments(new Geo().dashed(o.r, a0, span, 40, 0.5).build(), this._lineMat({ color: COLORS.amber, opacity: 0.6 }));
      } else {
        obj = new THREE.Mesh(new Geo().band(o.r, 0.014, a0, a0 + span, 0, 1, 48).build(), this._lineMat({ color: COLORS.gold, opacity: 0.45, fade: 1 }));
      }
      this._spinner(obj, this.face, { speed: o.speed, delay: 1.1 + rng() * 0.6 });
    }
  }

  _buildShockwaves() {
    this.shocks = [];
    for (let i = 0; i < 8; i++) {
      const mat = this._lineMat({ color: COLORS.gold, opacity: 0, bin: 0.05 });
      const mesh = new THREE.Mesh(new Geo().band(1.0, 0.012, 0, TAU, 0, 1, 160).build(), mat);
      mesh.visible = false;
      this.face.add(mesh);
      this.shocks.push({ mesh, mat, t: 1, strength: 0 });
    }
  }

  // Targeting marker projected onto the shell under the cursor.
  _buildMarker() {
    this.marker = new THREE.Group();
    this.markerMats = [];
    const mk = (obj, opacity) => {
      obj.material.userData.base = opacity;
      this.markerMats.push(obj.material);
      return obj;
    };
    const ring = mk(new THREE.Line(arcGeo(0.07, 0, TAU, 64), this._lineMat({ color: COLORS.hot, opacity: 0.9 })), 0.9);
    this.markerSpin = mk(
      new THREE.LineSegments(new Geo().dashed(0.105, 0, TAU, 8, 0.6).build(), this._lineMat({ color: COLORS.gold, opacity: 0.9 })),
      0.9,
    );
    const g = new Geo();
    for (let i = 0; i < 4; i++) {
      const a = (i * Math.PI) / 2;
      g.pos.push(Math.cos(a) * 0.13, Math.sin(a) * 0.13, 0, Math.cos(a) * 0.19, Math.sin(a) * 0.19, 0);
      g.t.push(0, 1);
    }
    const ticks = mk(new THREE.LineSegments(g.build(), this._lineMat({ color: COLORS.gold, opacity: 0.8 })), 0.8);
    this.markerDot = this._sprite(COLORS.hot, 0, 0.16);
    this.marker.add(ring, this.markerSpin, ticks, this.markerDot);
    this.marker.visible = false;
    this.markerAlpha = 0;
    this.group.add(this.marker);
  }

  _buildEnvironment() {
    const u = this.u;

    const N = 1800;
    const pos = new Float32Array(N * 3);
    const rnd = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      pos[i * 3] = rand(-14, 14);
      pos[i * 3 + 1] = rand(-6, 7);
      pos[i * 3 + 2] = rand(-20, 3);
      rnd[i * 3] = rng();
      rnd[i * 3 + 1] = rng();
      rnd[i * 3 + 2] = rng();
    }
    const dgeo = new THREE.BufferGeometry();
    dgeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    dgeo.setAttribute('aRand', new THREE.BufferAttribute(rnd, 3));
    const dust = new THREE.Points(
      dgeo,
      new THREE.ShaderMaterial({
        uniforms: { uTime: u.uTime, uPixelRatio: u.uPixelRatio, uColA: { value: COLORS.amber.clone() }, uColB: { value: COLORS.steel.clone() } },
        vertexShader: S.dustVert,
        fragmentShader: S.dustFrag,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    );
    dust.frustumCulled = false;
    this.env.add(dust);

    this.floor = new THREE.Mesh(
      new THREE.PlaneGeometry(80, 80).rotateX(-Math.PI / 2),
      new THREE.ShaderMaterial({
        uniforms: { uTime: u.uTime, uBass: u.uBass, uColor: { value: COLORS.steel.clone() }, uGlow: { value: COLORS.amber.clone() } },
        vertexShader: S.floorVert,
        fragmentShader: S.floorFrag,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    );
    this.floor.position.y = -2.2;
    this.env.add(this.floor);
  }

  // ------------------------------------------------------------- behaviour
  shock(strength = 1) {
    let s = this.shocks.find((x) => x.t >= 1);
    if (!s) s = this.shocks.reduce((a, b) => (a.t > b.t ? a : b));
    s.t = 0;
    s.strength = strength;
    s.mesh.visible = true;
    this.pulse = Math.min(1.4, this.pulse + strength * 0.5);
  }

  update(dt, t, audio, state) {
    const u = this.u;
    this.introT += dt;
    this.pulse *= Math.exp(-dt * 2.8);

    u.uTime.value = t;
    u.uBass.value = audio.bass;
    u.uMid.value = audio.mid;
    u.uTreble.value = audio.treble;
    u.uEnergy.value = state.energy;
    u.uPulse.value = this.pulse;
    u.uIntro.value = easeOutCubic(clamp01(this.introT / 2.6));
    const intro = u.uIntro.value;

    // rotation — everything spins faster with mids, hover energy and pulses
    const drive = 1 + audio.mid * 1.6 + state.energy * 1.2 + this.pulse * 2.5;
    this.spin.rotation.y += dt * (0.07 + audio.mid * 0.25 + state.energy * 0.2);
    this.spin.rotation.z = Math.sin(t * 0.13) * 0.12;
    this.inner.rotation.y -= dt * (0.25 + audio.bass * 0.6);
    this.inner.rotation.x += dt * 0.11;
    this.root.position.y = Math.sin(t * 0.6) * 0.03;

    const introRunning = this.introT < 6;
    for (const s of this.spinners) {
      s.obj.rotation.z += dt * s.speed * drive;
      if (s.tumble) s.holder.rotateX(dt * s.tumble * drive);
      if (introRunning) {
        const k = clamp01((this.introT - s.delay) / 0.9);
        s.obj.scale.setScalar(Math.max(0.0001, easeOutBack(k)));
        s.mat.uniforms.uOpacity.value = s.base * Math.min(1, k * 1.4);
      }
    }

    // core
    const b = audio.bass;
    this.coreHot.scale.setScalar((0.38 + b * 0.3 + this.pulse * 0.15 + Math.sin(t * 6) * 0.015) * intro + 0.0001);
    this.coreHalo.scale.setScalar((2.0 + b * 0.8 + state.energy * 0.6) * intro + 0.0001);
    this.coreHalo.material.opacity = 0.1 + audio.level * 0.12;
    this.coreBloom.material.opacity = 0.02 + b * 0.05;

    // waveform loop (mirrored so the seam closes cleanly)
    const w = audio.wave;
    const P = this.wavePos;
    const W = P.length / 3;
    for (let i = 0; i < W; i++) {
      const j = i < W / 2 ? i : W - 1 - i;
      const a = Math.PI / 2 - (i / W) * TAU;
      const r = (1.27 + w[j * 4] * 0.09) * intro;
      P[i * 3] = Math.cos(a) * r;
      P[i * 3 + 1] = Math.sin(a) * r;
    }
    this.waveLine.geometry.attributes.position.needsUpdate = true;

    // shockwaves
    for (const s of this.shocks) {
      if (s.t >= 1) continue;
      s.t = Math.min(1, s.t + dt / 1.25);
      s.mesh.scale.setScalar(0.5 + easeOutCubic(s.t) * 1.6);
      s.mat.uniforms.uOpacity.value = Math.pow(1 - s.t, 2) * s.strength * 1.3;
      if (s.t >= 1) s.mesh.visible = false;
    }

    // cursor marker
    this.markerAlpha += ((state.hovered ? 1 : 0) - this.markerAlpha) * Math.min(1, dt * 8);
    this.marker.visible = this.markerAlpha > 0.01;
    if (state.hovered) {
      this.marker.position.copy(state.hit);
      this.marker.lookAt(this._tmp.copy(state.hit).multiplyScalar(2));
    }
    this.markerSpin.rotation.z += dt * 2.5;
    this.marker.scale.setScalar((1 + Math.sin(t * 8) * 0.08) * (0.6 + 0.4 * this.markerAlpha));
    for (const m of this.markerMats) m.uniforms.uOpacity.value = m.userData.base * this.markerAlpha;
    this.markerDot.material.opacity = this.markerAlpha * 0.9;
  }
}
