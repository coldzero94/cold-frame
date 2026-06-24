<script setup lang="ts">
// Hybrid recall (BM25 + vector, RRF-fused) over the memory, with per-hit signal explainability.
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, type Band, type SearchHit } from '@/api'

const router = useRouter()
const q = ref('')
const hits = ref<SearchHit[]>([])
const loading = ref(false)
const error = ref('')
const searched = ref(false)
let seq = 0

const GLYPH: Record<Band, string> = { evergreen: '🌳', budding: '🌿', fading: '🌱' }

// debounce so we search as you pause typing, not on every keystroke
let timer: ReturnType<typeof setTimeout> | undefined
watch(q, (val) => {
  clearTimeout(timer)
  if (!val.trim()) {
    hits.value = []
    searched.value = false
    return
  }
  timer = setTimeout(() => run(val.trim()), 250)
})

async function run(query: string): Promise<void> {
  const mine = ++seq
  loading.value = true
  error.value = ''
  try {
    const resp = await api.search(query)
    if (mine !== seq) return // a newer query superseded this one
    hits.value = resp.hits
    searched.value = true
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    if (mine === seq) loading.value = false
  }
}

function sig(v: number | null): string {
  return v == null ? '—' : v.toFixed(2)
}
</script>

<template>
  <div class="h-full flex flex-col">
    <div class="p-4 sm:p-6 border-b border-line">
      <input
        v-model="q"
        type="search"
        placeholder="Search your memory…"
        aria-label="Search your memory"
        class="w-full bg-panel border border-line rounded-[10px] px-4 py-2.5 text-fg text-[14px] placeholder:text-dim focus:border-dim outline-none"
      />
    </div>

    <div class="flex-1 overflow-auto p-4 sm:p-6">
      <p v-if="loading" class="text-dim text-[13px]">searching…</p>
      <p v-else-if="error" class="text-ember text-[13px]">{{ error }}</p>
      <p v-else-if="searched && !hits.length" class="text-dim text-[13px]">
        No matches for “{{ q }}”.
      </p>
      <p v-else-if="!searched" class="text-dim text-[13px]">
        Hybrid recall — keyword and meaning, fused. Type to search.
      </p>

      <button
        v-for="h in hits"
        :key="h.id"
        type="button"
        class="block w-full text-left rounded-[10px] p-3 mb-2 border border-line bg-panel hover:border-dim transition-colors cursor-pointer"
        @click="router.push(`/fact/${h.id}`)"
      >
        <div class="flex gap-2 items-baseline">
          <span class="text-[15px]">{{ GLYPH[h.strength.band] || '·' }}</span>
          <span class="text-fg text-[13px] leading-snug flex-1">{{ h.content }}</span>
          <span class="text-dim text-[11px] font-mono shrink-0">{{ h.score.toFixed(3) }}</span>
        </div>
        <!-- signal explainability: how this hit ranked (semantic · bm25 · edge · rrf) -->
        <div class="text-[11px] text-dim mt-1.5 font-mono">
          semantic {{ sig(h.signals.semantic) }} · bm25 {{ sig(h.signals.bm25) }} ·
          edge {{ sig(h.signals.edge) }} · rrf {{ h.signals.rrf.toFixed(3) }}
        </div>
      </button>
    </div>
  </div>
</template>
