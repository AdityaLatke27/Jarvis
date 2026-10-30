import { defineConfig } from 'vite';

// The HUD is built into hud/dist and served by `python main.py --hud`.
export default defineConfig({
  build: {
    chunkSizeWarningLimit: 800, // three.js is one large chunk; that is expected
  },
});
