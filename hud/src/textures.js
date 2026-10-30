import * as THREE from 'three';

// Soft radial falloff used by the core glow sprites.
export function makeGlowTexture(size = 256) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  const h = size / 2;
  const grd = g.createRadialGradient(h, h, 0, h, h, h);
  grd.addColorStop(0.0, 'rgba(255,255,255,1)');
  grd.addColorStop(0.12, 'rgba(255,255,255,0.8)');
  grd.addColorStop(0.35, 'rgba(255,255,255,0.25)');
  grd.addColorStop(0.7, 'rgba(255,255,255,0.05)');
  grd.addColorStop(1.0, 'rgba(255,255,255,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(c);
}

// An atlas of little sci-fi readouts (hex dumps, bar charts, radar, graphs…)
// drawn white-on-black so the shader can use the red channel as a mask.
export function makeGlyphAtlas(rng, cells = 8, cell = 128) {
  const size = cells * cell;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  g.fillStyle = '#000';
  g.fillRect(0, 0, size, size);
  g.strokeStyle = '#fff';
  g.fillStyle = '#fff';
  g.textBaseline = 'top';
  const hex = (n) => Math.floor(rng() * 16 ** n).toString(16).toUpperCase().padStart(n, '0');

  for (let cy = 0; cy < cells; cy++) {
    for (let cx = 0; cx < cells; cx++) {
      const x = cx * cell + 8;
      const y = cy * cell + 8;
      const w = cell - 16;
      const h = cell - 16;
      g.save();
      g.beginPath();
      g.rect(x, y, w, h);
      g.clip();
      g.lineWidth = 2;
      g.globalAlpha = 1;

      switch ((cx + cy * 3) % 6) {
        case 0: {
          // hex dump
          g.font = '13px monospace';
          for (let i = 0; i < 7; i++) {
            g.globalAlpha = 0.45 + rng() * 0.55;
            const words = `${hex(4)} ${hex(2)} ${hex(4)}`;
            g.fillText(words.slice(0, 5 + Math.floor(rng() * 10)), x, y + i * 16);
          }
          break;
        }
        case 1: {
          // bar chart
          const n = 9;
          for (let i = 0; i < n; i++) {
            const bh = h * (0.12 + rng() * 0.85);
            g.fillRect(x + (i * w) / n + 2, y + h - bh, w / n - 4, bh);
          }
          break;
        }
        case 2: {
          // radar
          const px = x + w / 2;
          const py = y + h / 2;
          const R = w * 0.44;
          g.beginPath();
          g.arc(px, py, R, 0, Math.PI * 2);
          g.stroke();
          g.beginPath();
          g.arc(px, py, R * 0.6, 0, Math.PI * 2);
          g.stroke();
          g.beginPath();
          g.moveTo(px - R, py);
          g.lineTo(px + R, py);
          g.moveTo(px, py - R);
          g.lineTo(px, py + R);
          g.stroke();
          g.globalAlpha = 0.5;
          g.beginPath();
          g.moveTo(px, py);
          g.arc(px, py, R, -0.4 - rng(), -0.2);
          g.closePath();
          g.fill();
          break;
        }
        case 3: {
          // line graph
          g.strokeRect(x + 1, y + 1, w - 2, h - 2);
          g.beginPath();
          for (let i = 0; i <= 24; i++) {
            const px = x + (i * w) / 24;
            const py = y + h * (0.2 + rng() * 0.6);
            if (i) g.lineTo(px, py);
            else g.moveTo(px, py);
          }
          g.stroke();
          break;
        }
        case 4: {
          // bit matrix
          const n = 7;
          const s = w / n;
          for (let i = 0; i < n; i++) {
            for (let j = 0; j < n; j++) {
              if (rng() > 0.45) g.fillRect(x + i * s + 2, y + j * s + 2, s - 4, s - 4);
            }
          }
          break;
        }
        default: {
          // bracketed numeric readout
          const L = 16;
          g.beginPath();
          g.moveTo(x + 1, y + L);
          g.lineTo(x + 1, y + 1);
          g.lineTo(x + L, y + 1);
          g.moveTo(x + w - L, y + h - 1);
          g.lineTo(x + w - 1, y + h - 1);
          g.lineTo(x + w - 1, y + h - L);
          g.stroke();
          g.font = 'bold 36px monospace';
          g.fillText(String(Math.floor(rng() * 900 + 100)), x + 12, y + 24);
          g.font = '12px monospace';
          g.globalAlpha = 0.7;
          g.fillText(`${hex(2)}-${hex(3)}`, x + 12, y + 70);
          break;
        }
      }
      g.restore();
    }
  }

  const texture = new THREE.CanvasTexture(c);
  texture.anisotropy = 4;
  return { texture, cells };
}
