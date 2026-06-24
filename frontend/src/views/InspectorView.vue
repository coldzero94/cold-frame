<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, type Band, type FactDetail, type NoteBrief } from '@/api'

const route = useRoute()
const notes = ref<NoteBrief[]>([])
const detail = ref<FactDetail | null>(null)
const loading = ref(true)
const error = ref('')
const detailError = ref('')

// Record<Band> (not Record<string>) so the glyph table is proven exhaustive against the union.
const GLYPH: Record<Band, string> = { evergreen: '🌳', budding: '🌿', fading: '🌱' }

onMounted(async () => {
  try {
    notes.value = (await api.notes()).notes
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
})

watch(
  () => route.params.id,
  async (id) => {
    detailError.value = ''
    if (!id) {
      detail.value = null
      return
    }
    try {
      detail.value = await api.fact(String(id))
    } catch (e) {
      // surface it (mirrors the list path) — never swallow a 500/network error to a blank pane
      detail.value = null
      detailError.value = String(e)
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex h-full">
    <!-- list -->
    <div class="w-[420px] flex-shrink-0 border-r border-line overflow-auto p-4">
      <div class="text-[12px] tracking-wide text-dim mb-3 px-1">WHAT I KNOW ABOUT YOU NOW</div>
      <p v-if="loading" class="text-dim px-1">loading…</p>
      <p v-else-if="error" class="text-ember px-1">{{ error }}</p>
      <p v-else-if="!notes.length" class="text-dim px-1">No memories yet.</p>
      <RouterLink
        v-for="n in notes"
        :key="n.id"
        :to="`/fact/${n.id}`"
        class="block no-underline rounded-[10px] p-3 mb-2 border border-line bg-panel transition-colors hover:border-dim"
        :class="route.params.id === n.id ? 'border-ember!' : ''"
      >
        <div class="flex gap-2 items-baseline">
          <span class="text-[15px]">{{ GLYPH[n.strength.band] || '·' }}</span>
          <span class="text-fg text-[13px] leading-snug">{{ n.content }}</span>
        </div>
        <div class="h-[3px] rounded-full bg-ember/80 mt-2" :style="{ width: `${Math.round(n.strength.value * 100)}%` }" />
        <div class="text-[11px] text-dim mt-1.5">
          {{ n.memory_type }} · S={{ n.strength.value }}
          <span v-if="n.strength.at_risk" class="text-ember ml-1">○ at risk</span>
        </div>
      </RouterLink>
    </div>

    <!-- detail -->
    <div class="flex-1 min-w-0 overflow-auto p-7">
      <div v-if="detailError" class="text-ember h-full flex items-center justify-center">
        couldn't load this memory — {{ detailError }}
      </div>
      <div v-else-if="!detail" class="text-dim h-full flex items-center justify-center">
        Select a memory to see its provenance.
      </div>
      <div v-else class="max-w-[640px]">
        <div class="text-[20px] text-fg leading-snug mb-3">{{ detail.content }}</div>
        <div class="text-[12px] text-dim mb-6">
          {{ detail.memory_type }} · {{ detail.status }} · S={{ detail.strength.value }} ({{ detail.strength.band }})
          · confidence {{ detail.confidence }}
          <span v-if="detail.strength.at_risk" class="text-ember ml-1">· ○ at risk</span>
        </div>

        <div class="text-[12px] tracking-wide text-dim mb-2">PROVENANCE</div>
        <div v-for="(s, i) in detail.sources" :key="i" class="text-[13px] text-fg/90 mb-1">
          {{ s.kind }} · {{ s.ref }}<span v-if="s.role"> · {{ s.role }}</span>
          <span class="text-dim"> · {{ s.observed_at.slice(0, 10) }}</span>
        </div>
        <p v-if="!detail.sources.length" class="text-dim text-[13px]">—</p>

        <template v-if="detail.edges.length">
          <div class="text-[12px] tracking-wide text-dim mb-2 mt-6">EDGES</div>
          <div v-for="(e, i) in detail.edges" :key="i" class="text-[13px] text-fg/90 mb-1">
            {{ e.relation }} → {{ e.dst.slice(0, 8) }}
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
