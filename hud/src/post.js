import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

// Chromatic aberration, scanlines, vignette, grain and beat glitches.
const FinalShader = {
  uniforms: {
    tDiffuse: { value: null },
    uTime: { value: 0 },
    uAberration: { value: 0.002 },
    uGlitch: { value: 0 },
    uResolution: { value: new THREE.Vector2(1, 1) },
  },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform float uTime;
    uniform float uAberration;
    uniform float uGlitch;
    uniform vec2 uResolution;
    varying vec2 vUv;

    float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

    void main() {
      vec2 uv = vUv;
      float band = floor(uv.y * 48.0);
      float g = step(1.0 - uGlitch * 0.08, hash(vec2(band, floor(uTime * 24.0))));
      uv.x += g * (hash(vec2(band, uTime)) - 0.5) * 0.04 * uGlitch;

      vec2 dir = uv - 0.5;
      float d = length(dir);
      vec2 off = dir * uAberration * (0.4 + d);
      vec3 col;
      col.r = texture2D(tDiffuse, uv + off).r;
      col.g = texture2D(tDiffuse, uv).g;
      col.b = texture2D(tDiffuse, uv - off).b;

      col *= 0.965 + 0.035 * sin(gl_FragCoord.y * 1.6);
      col *= 1.0 - smoothstep(0.45, 1.05, d) * 0.85;
      col += (hash(gl_FragCoord.xy + fract(uTime) * 100.0) - 0.5) * 0.006;
      gl_FragColor = vec4(max(col, 0.0), 1.0);
    }
  `,
};

export function createPost(renderer, scene, camera) {
  const target = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 });
  const composer = new EffectComposer(renderer, target);
  composer.addPass(new RenderPass(scene, camera));

  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.8, 0.45, 0.12);
  composer.addPass(bloom);

  const final = new ShaderPass(FinalShader);
  composer.addPass(final);
  composer.addPass(new OutputPass());

  const post = {
    composer,
    bloom,
    final,
    setSize(width, height) {
      const pr = renderer.getPixelRatio();
      composer.setPixelRatio(pr);
      composer.setSize(width, height);
      final.uniforms.uResolution.value.set(width * pr, height * pr);
    },
    update(t, audio, state, pulse) {
      final.uniforms.uTime.value = t;
      final.uniforms.uAberration.value = 0.002 + audio.bass * 0.005 + state.glitch * 0.01;
      final.uniforms.uGlitch.value = state.glitch;
      bloom.strength = 0.62 + audio.bass * 0.2 + state.energy * 0.12 + pulse * 0.15;
    },
    render() {
      composer.render();
    },
  };
  post.setSize(window.innerWidth, window.innerHeight);
  return post;
}
