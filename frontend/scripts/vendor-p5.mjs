// Copy p5 from the locked node_modules into public/ so Vite ships it as a same-origin asset.
// Done at build time (not committed) → the repo stays lean and p5's version tracks pnpm-lock.
import { copyFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

const src = fileURLToPath(new URL('../node_modules/p5/lib/p5.min.js', import.meta.url))
const pub = fileURLToPath(new URL('../public', import.meta.url))
mkdirSync(pub, { recursive: true })
copyFileSync(src, `${pub}/p5.min.js`)
console.log('vendored p5.min.js → public/')
