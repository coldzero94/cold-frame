import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'

// Build the SPA into the Python package's _dist/ so the wheel ships it (artifacts include).
// p5 + Vue are BUNDLED into the output (no externals, no CDN) so the runtime fetches nothing — I5.
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
