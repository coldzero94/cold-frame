import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'

// Build the SPA into the Python package's _dist/ so the wheel ships it (artifacts include).
// Vue + the app are bundled; p5 is vendored into public/ (emitted as a same-origin _dist/p5.min.js
// asset by vendor-p5.mjs, not an external/CDN). Every runtime fetch is same-origin — I5.
export default defineConfig({
  plugins: [vue(), UnoCSS()],
  base: '/',
  build: {
    outDir: fileURLToPath(new URL('../cold_frame/ui/_dist', import.meta.url)),
    emptyOutDir: true,
    target: 'es2020',
    chunkSizeWarningLimit: 1200,
  },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
})
