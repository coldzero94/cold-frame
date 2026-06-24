// The thermal memory-field p5 sketch (philosophy: cold_frame/ui/prototype/thermal-persistence.md).
// A pure factory: no Vue dependency — it takes the container, the data, and a hover callback, and
// returns a p5 instance-mode sketch + a reseed handle. Position is id-hash stable for a given seed
// (spatial-memory law); only heat/opacity/turbulence animate — never reposition on data updates
// (resize and the explicit reseed are the only re-layouts). p5 is the runtime global from
// /p5.min.js; we use the type only here. (Ported from the prototype's standalone sketch.)
import type P5 from 'p5'
import type { FieldNote } from '@/api'

interface Ember {
  d: FieldNote
  x: number
  y: number
  phase: number
  nseed: number
}
type Rgb = { r: number; g: number; b: number }

function hash01(str: string, salt: number): number {
  let h = 2166136261 ^ salt
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  h ^= h >>> 13
  h = Math.imul(h, 0x5bd1e995)
  h ^= h >>> 15
  return ((h >>> 0) % 100000) / 100000
}
function hexToRgb(hex: string): Rgb {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex)!
  return { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) }
}
function lerpRgb(a: Rgb, b: Rgb, t: number): Rgb {
  return { r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t }
}

const STOPS = { cold: hexToRgb('#6a9bcc'), ember: hexToRgb('#d97757'), warm: hexToRgb('#f3d9a4') }
const P = { bloom: 1.15, turbulence: 0.7, coreBright: 1.05, spread: 1.0, decayContrast: 1.0, frameGlow: 1.0 }
const GOLDEN = Math.PI * (3 - Math.sqrt(5))

function heatColor(s: number): Rgb {
  const slate = { r: 42, g: 54, b: 74 }
  if (s < 0.5) return lerpRgb(slate, STOPS.cold, Math.pow(s / 0.5, 0.85))
  if (s < 0.8) return lerpRgb(STOPS.cold, STOPS.ember, (s - 0.5) / 0.3)
  return lerpRgb(STOPS.ember, STOPS.warm, (s - 0.8) / 0.2)
}

export interface ThermalHandle {
  /** the p5 sketch function — pass to `new window.p5(handle.sketch)`. */
  sketch: (p: P5) => void
  /** re-roll the field angle: same memory, a different view. */
  reseed: () => void
}

export function makeThermalField(
  el: HTMLElement,
  data: FieldNote[],
  onHover: (n: FieldNote | null) => void,
): ThermalHandle {
  let doReseed: () => void = () => {}

  const sketch = (p: P5) => {
    let W = 0
    let H = 0
    let embers: Ember[] = []
    let seed = 7
    const ctx = () => p.drawingContext as CanvasRenderingContext2D

    function layout() {
      p.noiseSeed(seed)
      const ranked = data.map((d) => ({ d, key: hash01(d.id, 1) })).sort((a, b) => a.key - b.key)
      const n = ranked.length || 1
      const cx = W / 2
      const cy = H / 2
      const maxR = Math.min(W, H) * 0.42 * P.spread
      const rot = seed * 0.618
      embers = ranked.map((e, rank) => {
        const r = Math.sqrt((rank + 0.5) / n) * maxR
        const a = rank * GOLDEN + rot
        const jx = (hash01(e.d.id, 7) - 0.5) * (maxR * 0.05)
        const jy = (hash01(e.d.id, 13) - 0.5) * (maxR * 0.05)
        return {
          d: e.d,
          x: cx + Math.cos(a) * r + jx,
          y: cy + Math.sin(a) * r + jy,
          phase: hash01(e.d.id, 21) * 1000,
          nseed: hash01(e.d.id, 31) * 500,
        }
      })
    }

    function shimmer(e: Ember, t: number): number {
      const d = e.d
      const anxiety = (d.atRisk ? 1.7 : 1.0) * (1.15 - d.s * 0.6)
      const nz = p.noise(e.nseed, t * (d.atRisk ? 2.2 : 1.0) + e.phase)
      let s = 1 + (nz - 0.5) * P.turbulence * anxiety * 0.7
      if (d.atRisk && p.noise(e.nseed + 99, t * 3.3) > 0.78) s *= 0.72
      return s
    }

    function bloom(e: Ember, t: number) {
      const d = e.d
      const sh = shimmer(e, t)
      const bright = Math.pow(p.map(d.s, 0, 1, 0.28, 1.0), 1 / P.decayContrast)
      const c = heatColor(d.s)
      const R = (22 + d.s * 72 + d.importance * 34) * P.bloom * sh
      const a = 0.48 * bright * P.coreBright
      const g = ctx().createRadialGradient(e.x, e.y, 0, e.x, e.y, R)
      g.addColorStop(0, `rgba(${c.r | 0},${c.g | 0},${c.b | 0},${a})`)
      g.addColorStop(0.32, `rgba(${c.r | 0},${c.g | 0},${c.b | 0},${a * 0.38})`)
      g.addColorStop(1, `rgba(${c.r | 0},${c.g | 0},${c.b | 0},0)`)
      ctx().fillStyle = g
      ctx().beginPath()
      ctx().arc(e.x, e.y, R, 0, Math.PI * 2)
      ctx().fill()
    }

    function coldFrame(e: Ember, coreR: number, c: Rgb) {
      const R = coreR * 3.4 + 10
      const a = 70 * P.frameGlow
      p.push()
      p.translate(e.x, e.y)
      p.rotate(hash01(e.d.id, 5) * Math.PI)
      ctx().globalCompositeOperation = 'lighter'
      p.noStroke()
      p.fill(c.r, c.g, c.b, 14 * P.frameGlow)
      p.beginShape()
      for (let k = 0; k < 6; k++) p.vertex(Math.cos((k * Math.PI) / 3) * R, Math.sin((k * Math.PI) / 3) * R)
      p.endShape(p.CLOSE)
      ctx().globalCompositeOperation = 'source-over'
      p.noFill()
      p.stroke(232, 230, 220, a)
      p.strokeWeight(1.1)
      p.beginShape()
      for (let k = 0; k < 6; k++) p.vertex(Math.cos((k * Math.PI) / 3) * R, Math.sin((k * Math.PI) / 3) * R)
      p.endShape(p.CLOSE)
      p.stroke(255, 255, 250, a * 0.7)
      p.strokeWeight(0.6)
      for (let k = 0; k < 6; k++) p.line(0, 0, Math.cos((k * Math.PI) / 3) * R, Math.sin((k * Math.PI) / 3) * R)
      p.pop()
    }

    function frost(e: Ember, coreR: number, t: number) {
      const R = coreR * 2 + 9
      p.stroke(180, 210, 240, 95)
      p.strokeWeight(1)
      for (let k = 0; k < 18; k++) {
        const ang = (k / 18) * Math.PI * 2
        const tr = (p.noise(e.nseed + k, t * 4) - 0.5) * 5
        p.line(
          e.x + Math.cos(ang) * (R + tr),
          e.y + Math.sin(ang) * (R + tr),
          e.x + Math.cos(ang) * (R + 3 + tr),
          e.y + Math.sin(ang) * (R + 3 + tr),
        )
      }
    }

    function core(e: Ember, t: number) {
      const d = e.d
      const sh = shimmer(e, t)
      const bright = Math.pow(p.map(d.s, 0, 1, 0.28, 1.0), 1 / P.decayContrast)
      const c = heatColor(d.s)
      const coreR = (2.2 + d.s * 5.5 + d.importance * 2.5) * (0.85 + sh * 0.2)
      if (d.pinned) coldFrame(e, coreR, c)
      if (d.atRisk) frost(e, coreR, t)
      const wc = lerpRgb(c, { r: 255, g: 248, b: 232 }, 0.55 * d.s)
      p.noStroke()
      ctx().globalCompositeOperation = 'lighter'
      p.fill(wc.r, wc.g, wc.b, 235 * bright * P.coreBright)
      p.circle(e.x, e.y, coreR * 2)
      p.fill(255, 250, 240, 200 * bright * d.s * P.coreBright)
      p.circle(e.x, e.y, coreR * 0.9)
      ctx().globalCompositeOperation = 'source-over'
    }

    function hover() {
      if (p.mouseX < 0 || p.mouseX > W || p.mouseY < 0 || p.mouseY > H) {
        onHover(null)
        return
      }
      let best: FieldNote | null = null
      let bd = 34 * 34
      for (const e of embers) {
        const dd = (e.x - p.mouseX) ** 2 + (e.y - p.mouseY) ** 2
        if (dd < bd) {
          bd = dd
          best = e.d
        }
      }
      onHover(best)
    }

    p.setup = () => {
      W = el.clientWidth
      H = el.clientHeight
      p.createCanvas(W, H).parent(el)
      layout()
    }
    p.windowResized = () => {
      W = el.clientWidth
      H = el.clientHeight
      p.resizeCanvas(W, H)
      layout() // resize is a viewport change (allowed), not a data transition
    }
    p.draw = () => {
      const t = p.millis() * 0.0006
      p.background(16, 16, 14)
      const c = ctx()
      c.save()
      const vg = c.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.06, W / 2, H / 2, Math.min(W, H) * 0.62)
      vg.addColorStop(0, 'rgba(46,34,24,0.62)')
      vg.addColorStop(0.6, 'rgba(26,22,18,0.28)')
      vg.addColorStop(1, 'rgba(9,9,8,0)')
      c.fillStyle = vg
      c.fillRect(0, 0, W, H)
      c.restore()
      c.save()
      c.globalCompositeOperation = 'lighter'
      for (const e of embers) bloom(e, t)
      c.restore()
      for (const e of embers) core(e, t)
      hover()
    }

    doReseed = () => {
      seed += 1
      layout()
    }
  }

  return { sketch, reseed: () => doReseed() }
}
