<script setup lang="ts">
// The hero: a thermal memory field (philosophy: cold_frame/ui/prototype/thermal-persistence.md).
// A thin host for the p5 instance-mode sketch (thermalSketch.ts), fed live by /api/memory-field.
// p5 is the runtime global from a vendored same-origin /p5.min.js (the import is TYPE-ONLY, erased):
// no CDN → I5, and every runtime fetch (app bundle, /p5.min.js, the API) is same-origin.
import type P5 from 'p5'
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { api, type FieldNote } from '@/api'
import { makeThermalField } from './thermalSketch'
import EmptyState from './EmptyState.vue'

const router = useRouter()

declare global {
  interface Window {
    p5: typeof P5
  }
}

const host = ref<HTMLDivElement>()
const notes = shallowRef<FieldNote[]>([])
const total = ref(0)
const hovered = ref<FieldNote | null>(null)
const loading = ref(true)
const error = ref('')
let instance: P5 | null = null
let reseed: (() => void) | null = null

const counts = computed(() => {
  const c = { evergreen: 0, budding: 0, fading: 0, pinned: 0, atRisk: 0 }
  for (const n of notes.value) {
    c[n.band]++
    if (n.pinned) c.pinned++
    if (n.atRisk) c.atRisk++
  }
  return c
})

const fieldLabel = computed(() => {
  if (loading.value) return 'Memory field, loading'
  if (!notes.value.length) return 'Memory field, empty'
  const c = counts.value
  return `Memory field: ${c.evergreen} evergreen, ${c.budding} budding, ${c.fading} fading, ${c.pinned} sheltered, ${c.atRisk} at-risk. Full detail in the Inspector.`
})

onMounted(async () => {
  try {
    const resp = await api.memoryField()
    notes.value = resp.notes
    total.value = resp.total
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
  if (!host.value) return
  if (typeof window.p5 !== 'function') {
    error.value = 'the visualization runtime failed to load' // /p5.min.js missing/blocked
    return
  }
  const field = makeThermalField(
    host.value,
    notes.value,
    (n) => (hovered.value = n),
    (n) => router.push(`/fact/${n.id}`), // click an ember → open the memory (no longer a dead-end)
  )
  instance = new window.p5(field.sketch)
  reseed = field.reseed
})

onBeforeUnmount(() => {
  instance?.remove()
  instance = null
  reseed = null
})
</script>

<template>
  <div class="relative h-full w-full overflow-hidden bg-ink">
    <!-- the canvas is an ambient view; its per-note data is fully available in the Inspector.
         role=img + a live summary give AT users the field at a glance (ux-design.md a11y intent). -->
    <div ref="host" class="absolute inset-0" role="img" :aria-label="fieldLabel" />

    <!-- cold-start: a centered planting card over the dark field until the first memory lands -->
    <div
      v-if="!loading && !error && !notes.length"
      class="absolute inset-0 flex items-center justify-center"
    >
      <EmptyState />
    </div>

    <!-- legend (read-only; reflects the live snapshot) — hidden when the field is empty -->
    <div v-if="notes.length" class="absolute top-5 left-6 text-[13px] select-none pointer-events-none">
      <div class="text-[12px] tracking-[0.08em] text-dim mb-3">YOUR MEMORY FIELD</div>
      <div class="flex items-center gap-2 mb-1.5" style="color: #f0cf8e">
        <span class="legend-dot" />Evergreen<span class="ml-2 text-dim font-mono">{{ counts.evergreen }}</span>
      </div>
      <div class="flex items-center gap-2 mb-1.5" style="color: #d97757">
        <span class="legend-dot" />Budding<span class="ml-2 text-dim font-mono">{{ counts.budding }}</span>
      </div>
      <div class="flex items-center gap-2" style="color: #6a9bcc">
        <span class="legend-dot" />Fading<span class="ml-2 text-dim font-mono">{{ counts.fading }}</span>
      </div>
      <div class="text-[11px] text-dim mt-3 leading-relaxed">
        ⬡ {{ counts.pinned }} sheltered &nbsp;·&nbsp; ❄ {{ counts.atRisk }} at-risk
      </div>
      <div v-if="notes.length < total" class="text-[11px] text-dim/80 mt-1.5">
        showing {{ notes.length }} of {{ total }}
      </div>
    </div>

    <!-- reshuffle the field angle (same memory, different view) — hidden when empty -->
    <button
      v-if="notes.length"
      class="absolute top-5 right-6 text-dim text-[12px] px-3 py-1.5 rounded-[8px] border border-line bg-panel/70 hover:text-fg hover:border-dim transition-colors"
      title="Reshuffle the field — same memory, a different angle"
      @click="reseed?.()"
    >
      ↻ angle
    </button>

    <!-- hover read-out (calm, fixed — no chasing tooltip) -->
    <div class="absolute bottom-0 left-0 right-0 p-6 pointer-events-none" aria-live="polite">
      <div
        v-if="hovered"
        class="inline-block max-w-[70%] rounded-[10px] border-l-2 border-ember bg-ink/85 px-4 py-3 backdrop-blur-sm"
      >
        <div class="text-fg text-[15px] leading-snug">{{ hovered.content }}</div>
        <div class="text-dim text-[12px] mt-1">
          {{ hovered.band }} · S={{ hovered.s.toFixed(2) }} · {{ hovered.type }} · ×{{ hovered.access }}
          <span v-if="hovered.pinned"> · ⬡ pinned</span>
          <span v-if="hovered.atRisk" class="text-ember"> · ❄ at-risk</span>
        </div>
      </div>
      <div v-else-if="loading" class="text-dim text-[12px]">Kindling your memory field…</div>
      <div v-else-if="!error && notes.length" class="text-dim text-[12px]">
        Hover an ember to read it, click to open. Warmth is belief; the cold is forgetting.
      </div>
      <div v-else-if="error" class="text-ember text-[13px]">{{ error }}</div>
    </div>
  </div>
</template>

<style scoped>
.legend-dot {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 7px currentColor;
  flex-shrink: 0;
}
</style>
