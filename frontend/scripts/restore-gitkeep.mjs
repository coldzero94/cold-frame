// vite build (emptyOutDir) wipes _dist including the tracked .gitkeep; recreate it so the dir
// stays present in git (fresh clones + the wheel build need it) without diff-noise.
import { writeFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

writeFileSync(fileURLToPath(new URL('../../cold_frame/ui/_dist/.gitkeep', import.meta.url)), '')
