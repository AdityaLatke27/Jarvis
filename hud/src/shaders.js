// GLSL for every custom material in the hologram.
// All of them are drawn with additive blending, so alpha acts as intensity.

// ---------------------------------------------------------------------------
// Particle shell — audio pushes points outward, the cursor ray parts them.
// ---------------------------------------------------------------------------
export const pointsVert = /* glsl */ `
uniform float uTime;
uniform float uBass;
uniform float uTreble;
uniform float uEnergy;
uniform float uPulse;
uniform float uIntro;
uniform float uPixelRatio;
uniform float uSize;
uniform float uMouseForce;
uniform sampler2D uSpectrum;
uniform vec3 uRayO;
uniform vec3 uRayD;

attribute vec4 aRand;

varying float vAlpha;
varying float vHeat;

void main() {
  vec3 p = position;
  float r = length(p);
  vec3 n = p / max(r, 1e-4);
  float spec = texture2D(uSpectrum, vec2(aRand.x, 0.5)).r;

  float intro = smoothstep(aRand.w * 0.55, aRand.w * 0.55 + 0.45, uIntro);
  p *= intro;
  p += n * (spec * 0.2 * aRand.y + uBass * 0.05 * r + uPulse * 0.3 * aRand.z) * intro;
  p += 0.012 * vec3(
    sin(uTime * 1.3 + aRand.w * 21.0),
    cos(uTime * 1.1 + aRand.z * 17.0),
    sin(uTime * 0.9 + aRand.y * 13.0));

  vec4 wp = modelMatrix * vec4(p, 1.0);

  // push particles away from the cursor ray
  vec3 toP = wp.xyz - uRayO;
  vec3 closest = uRayO + uRayD * dot(toP, uRayD);
  vec3 away = wp.xyz - closest;
  float d = length(away);
  float infl = (1.0 - smoothstep(0.0, 0.42, d)) * uMouseForce;
  wp.xyz += (away / max(d, 1e-4)) * infl * 0.2;

  vec4 mv = viewMatrix * wp;
  gl_Position = projectionMatrix * mv;

  float tw = 0.55 + 0.45 * sin(uTime * (1.5 + aRand.z * 5.0) + aRand.w * 40.0);
  float size = uSize * (0.35 + aRand.y * 1.1) * (1.0 + spec * 0.6 + infl * 0.8 + uTreble * 0.3);
  gl_PointSize = size * uPixelRatio / -mv.z;

  vAlpha = intro * tw * (0.09 + spec * 0.22 + infl * 0.35 + uEnergy * 0.12);
  vHeat = clamp(spec * 0.8 + infl * 0.5 + (1.0 - min(r, 1.0)) * 0.5, 0.0, 1.0);
}
`;

export const pointsFrag = /* glsl */ `
uniform vec3 uColA;
uniform vec3 uColB;
varying float vAlpha;
varying float vHeat;

void main() {
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5) discard;
  float a = pow(1.0 - d * 2.0, 1.6);
  gl_FragColor = vec4(mix(uColA, uColB, vHeat), a * vAlpha);
}
`;

// ---------------------------------------------------------------------------
// Lines / bands — arcs, dials, filaments. aT runs 0..1 along each element so
// we can fade tails and run bright "data pulses" along them.
// ---------------------------------------------------------------------------
export const lineVert = /* glsl */ `
attribute float aT;
varying float vT;

void main() {
  vT = aT;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

export const lineFrag = /* glsl */ `
uniform vec3 uColor;
uniform float uOpacity;
uniform float uTime;
uniform float uFade;
uniform float uBin;
uniform float uHeadSpeed;
uniform float uHeadOffset;
uniform float uEnergy;
uniform float uFlicker;
uniform sampler2D uSpectrum;
varying float vT;

void main() {
  float s = texture2D(uSpectrum, vec2(uBin, 0.5)).r;
  float fade = mix(1.0, vT * vT, uFade);
  float head = fract(uTime * uHeadSpeed + uHeadOffset);
  float glow = uHeadSpeed == 0.0 ? 0.0 : exp(-abs(vT - head) * 38.0);
  float n = fract(sin(floor(uTime * 16.0) * 12.9898 + uHeadOffset * 78.233) * 43758.5453);
  float flick = 1.0 - uFlicker * step(0.9, n);
  float a = uOpacity * flick * (fade * (0.5 + s * 0.6 + uEnergy * 0.3) + glow * 1.6);
  gl_FragColor = vec4(uColor * (1.0 + glow * 0.8), a);
}
`;

// ---------------------------------------------------------------------------
// Radial spikes that shoot off the shell with the spectrum.
// ---------------------------------------------------------------------------
export const spikeVert = /* glsl */ `
uniform float uTime;
uniform float uIntro;
uniform float uPulse;
uniform sampler2D uSpectrum;
attribute float aR0;
attribute float aLen;
attribute float aEnd;
attribute vec2 aRand;
varying float vA;

void main() {
  float s = texture2D(uSpectrum, vec2(aRand.x, 0.5)).r;
  float flick = 0.55 + 0.45 * sin(uTime * (2.0 + aRand.y * 7.0) + aRand.x * 60.0);
  float len = aLen * (0.2 + s * 1.6 + uPulse * 0.8);
  float r = (aR0 + aEnd * len) * uIntro;
  vA = (1.0 - aEnd) * (0.15 + s * 0.45) * flick * uIntro;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position * r, 1.0);
}
`;

export const spikeFrag = /* glsl */ `
uniform vec3 uColor;
varying float vA;

void main() {
  gl_FragColor = vec4(uColor, vA);
}
`;

// ---------------------------------------------------------------------------
// Circular spectrum bars on the screen-facing instrument ring.
// ---------------------------------------------------------------------------
export const barsVert = /* glsl */ `
uniform sampler2D uSpectrum;
uniform float uR0;
uniform float uLen;
uniform float uHalfW;
uniform float uIntro;
attribute float aAngle;
attribute float aSide;
attribute float aEnd;
attribute float aBin;
varying float vEnd;
varying float vS;

void main() {
  float s = texture2D(uSpectrum, vec2(aBin, 0.5)).r;
  float r = uR0 + aEnd * (0.01 + s * uLen);
  float ang = aAngle + aSide * uHalfW + (1.0 - uIntro) * 2.0;
  vEnd = aEnd;
  vS = s;
  vec3 p = vec3(cos(ang), sin(ang), 0.0) * r * uIntro;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}
`;

export const barsFrag = /* glsl */ `
uniform vec3 uColA;
uniform vec3 uColB;
varying float vEnd;
varying float vS;

void main() {
  vec3 c = mix(uColA, uColB, clamp(vEnd * vS * 1.2, 0.0, 1.0));
  gl_FragColor = vec4(c, (0.22 + vS * 0.55) * mix(1.0, 0.6, vEnd));
}
`;

// ---------------------------------------------------------------------------
// Floating data panels (text blocks, graphs, radar glyphs) on the shell.
// ---------------------------------------------------------------------------
export const panelVert = /* glsl */ `
uniform float uIntro;
attribute float aSeed;
varying vec2 vUv;
varying float vSeed;

void main() {
  vUv = uv;
  vSeed = aSeed;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position * uIntro, 1.0);
}
`;

export const panelFrag = /* glsl */ `
uniform sampler2D uAtlas;
uniform vec3 uColor;
uniform float uTime;
uniform float uTreble;
uniform float uEnergy;
varying vec2 vUv;
varying float vSeed;

float hash(float n) { return fract(sin(n) * 43758.5453); }

void main() {
  float tex = texture2D(uAtlas, vUv).r;
  float on = step(0.22, hash(floor(uTime * (0.6 + vSeed * 2.5)) + vSeed * 91.7));
  float scan = 0.75 + 0.25 * sin(gl_FragCoord.y * 1.4 + uTime * 6.0);
  float a = tex * on * scan * (0.32 + uTreble * 0.5 + uEnergy * 0.3);
  gl_FragColor = vec4(uColor, a);
}
`;

// ---------------------------------------------------------------------------
// Ambient dust drifting through the room.
// ---------------------------------------------------------------------------
export const dustVert = /* glsl */ `
uniform float uTime;
uniform float uPixelRatio;
attribute vec3 aRand;
varying float vA;
varying float vMix;

void main() {
  vec3 p = position;
  p.x += sin(uTime * 0.07 + aRand.x * 6.283) * 0.6;
  p.y += sin(uTime * 0.11 + aRand.y * 6.283) * 0.4;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = (5.0 + aRand.z * 14.0) * uPixelRatio / -mv.z;
  float tw = 0.5 + 0.5 * sin(uTime * (0.4 + aRand.x * 1.5) + aRand.y * 40.0);
  vA = (0.12 + 0.3 * tw) * (1.0 - smoothstep(4.0, 40.0, -mv.z));
  vMix = aRand.z;
}
`;

export const dustFrag = /* glsl */ `
uniform vec3 uColA;
uniform vec3 uColB;
varying float vA;
varying float vMix;

void main() {
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5) discard;
  gl_FragColor = vec4(mix(uColA, uColB, step(0.7, vMix)), (1.0 - d * 2.0) * vA);
}
`;

// ---------------------------------------------------------------------------
// Lab floor grid with a warm reflection under the hologram.
// ---------------------------------------------------------------------------
export const floorVert = /* glsl */ `
varying vec3 vW;

void main() {
  vec4 w = modelMatrix * vec4(position, 1.0);
  vW = w.xyz;
  gl_Position = projectionMatrix * viewMatrix * w;
}
`;

export const floorFrag = /* glsl */ `
uniform vec3 uColor;
uniform vec3 uGlow;
uniform float uTime;
uniform float uBass;
varying vec3 vW;

void main() {
  vec2 g = vW.xz * 1.25;
  vec2 fw = fwidth(g);
  vec2 grid = abs(fract(g - 0.5) - 0.5) / fw;
  // drop lines once cells get sub-pixel, otherwise the horizon turns solid
  float line = (1.0 - min(min(grid.x, grid.y), 1.0)) * (1.0 - smoothstep(0.15, 0.6, max(fw.x, fw.y)));
  float dist = length(vW.xz);
  float fade = exp(-dist * 0.11);
  float ripple = 0.55 + 0.45 * sin(dist * 2.4 - uTime * 1.6);
  vec3 col = uColor * line * fade * ripple * 0.35;
  col += uGlow * exp(-dist * dist * 0.08) * (0.03 + uBass * 0.08);
  gl_FragColor = vec4(col, 1.0);
}
`;
