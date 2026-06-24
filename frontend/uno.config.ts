import { defineConfig, presetUno } from 'unocss'

// Atomic CSS (antfu stack, D20). Dark-minimal, calm — the design law is "state is the hero".
export default defineConfig({
  presets: [presetUno()],
  theme: {
    colors: {
      ink: '#0b0b0f',
      panel: '#101015',
      line: '#1c1c22',
      dim: '#6f6f78',
      fg: '#e7e7ea',
      ember: '#d97757',
      cold: '#6a9bcc',
      warm: '#f0cf8e',
    },
    fontFamily: {
      sans: 'Inter, -apple-system, system-ui, sans-serif',
    },
  },
})
