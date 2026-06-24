<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, type Band, type FactDetail, type HistoryVersion, type NoteBrief } from '@/api'
import EmptyState from '@/components/EmptyState.vue'

const route = useRoute()
const router = useRouter()
const busy = ref(false)
const correcting = ref(false)
const correctText = ref('')
const adding = ref(false)
const addText = ref('')
const addError = ref('')
const notes = ref<NoteBrief[]>([])
const total = ref(0)
const detail = ref<FactDetail | null>(null)
const history = ref<HistoryVersion[]>([])
const loading = ref(true)
const error = ref('')
const detailError = ref('')

// Record<Band> (not Record<string>) so the glyph table is proven exhaustive against the union.
const GLYPH: Record<Band, string> = { evergreen: '🌳', budding: '🌿', fading: '🌱' }

// Confidence as a discrete glyph in ambient surfaces (ux-design §8.1/§8.3: one signal → one channel,
// decimals only in the Fact Detail header). ● high · ◐ medium · ○ low.
function confGlyph(c: number): string {
  return c >= 0.8 ? '●' : c >= 0.4 ? '◐' : '○'
}

// Edges get a per-relation channel (§8.5): supersedes is the belief-change story, not a flat list.
const EDGE_STYLE: Record<string, { icon: string; cls: string }> = {
  supersedes: { icon: '⇒', cls: 'text-ember' },
  duplicate_of: { icon: '≈', cls: 'text-cold' },
  relates_to: { icon: '→', cls: 'text-dim' },
}
function edgeStyle(rel: string): { icon: string; cls: string } {
  return EDGE_STYLE[rel] ?? { icon: '→', cls: 'text-dim' }
}

onMounted(async () => {
  try {
    const resp = await api.notes()
    notes.value = resp.notes
    total.value = resp.total
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
})

watch(
  () => route.params.id,
  async (id) => {
    detailError.value = ''
    history.value = []
    if (!id) {
      detail.value = null
      return
    }
    try {
      detail.value = await api.fact(String(id))
      history.value = (await api.factHistory(String(id))).versions
    } catch (e) {
      // surface it (mirrors the list path) — never swallow a 500/network error to a blank pane
      detail.value = null
      detailError.value = e instanceof Error ? e.message : String(e)
    }
  },
  { immediate: true },
)

async function refresh(): Promise<void> {
  const resp = await api.notes()
  notes.value = resp.notes
  total.value = resp.total
  const id = route.params.id
  if (id) {
    detail.value = await api.fact(String(id))
    history.value = (await api.factHistory(String(id))).versions
  }
}

// run a mutation, then refresh the list + detail so the view reflects the new truth
async function act(fn: () => Promise<unknown>): Promise<void> {
  busy.value = true
  detailError.value = ''
  try {
    await fn()
    await refresh()
  } catch (e) {
    detailError.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function submitAdd(): Promise<void> {
  const text = addText.value.trim()
  if (!text) return
  busy.value = true
  addError.value = ''
  try {
    const res = await api.create(text)
    if (res.blocked.length) {
      // a secret was BLOCKed pre-disk (I6): 200, but nothing stored — tell the user why
      addError.value = "That looked like a secret, so it wasn't stored."
    } else {
      addText.value = ''
      adding.value = false
    }
    await refresh()
  } catch (e) {
    addError.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function submitCorrect(): Promise<void> {
  const text = correctText.value.trim()
  if (!text) return
  busy.value = true
  detailError.value = ''
  try {
    const created = await api.correct(String(route.params.id), text)
    correcting.value = false
    correctText.value = ''
    await router.push(`/fact/${created.id}`) // follow the supersede to the new fact
  } catch (e) {
    detailError.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <!-- cold-start: when there's genuinely nothing, the planting card replaces the two-pane layout -->
  <div v-if="!loading && !error && !notes.length" class="h-full flex items-center justify-center">
    <EmptyState />
  </div>

  <!-- desktop: list + detail side-by-side. mobile: one at a time (list, tap → detail). -->
  <div v-else class="flex h-full">
    <!-- list -->
    <div
      class="w-full sm:w-[420px] sm:flex-shrink-0 border-r border-line overflow-auto p-4"
      :class="route.params.id ? 'hidden sm:block' : ''"
    >
      <div class="flex items-baseline justify-between mb-3 px-1">
        <span class="text-[12px] tracking-wide text-dim">WHAT I KNOW ABOUT YOU NOW</span>
        <button
          type="button"
          class="text-[12px] text-dim hover:text-fg bg-transparent border-0 cursor-pointer"
          :title="adding ? 'Cancel' : 'Add a memory'"
          @click="adding = !adding"
        >
          {{ adding ? '×' : '+ add' }}
          <span v-if="!adding && total" class="text-[11px] font-mono ml-1">
            {{ notes.length < total ? `${notes.length} of ${total}` : total }}
          </span>
        </button>
      </div>
      <div v-if="adding" class="mb-3 px-1">
        <input
          v-model="addText"
          placeholder="remember that…"
          aria-label="Add a memory"
          class="w-full bg-panel border border-line rounded-[8px] px-3 py-2 text-fg text-[13px] outline-none focus:border-dim"
          @keyup.enter="submitAdd"
        />
        <p v-if="addError" class="text-ember text-[11px] mt-1">{{ addError }}</p>
      </div>
      <p v-if="loading" class="text-dim px-1">loading…</p>
      <p v-else-if="error" class="text-ember px-1">{{ error }}</p>
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
          {{ n.memory_type }}
          <span :title="`confidence ${n.confidence}`" class="ml-1">{{ confGlyph(n.confidence) }}</span>
          <span v-if="n.strength.at_risk" class="text-ember ml-1">❄ at risk</span>
        </div>
      </RouterLink>
    </div>

    <!-- detail -->
    <div
      class="flex-1 min-w-0 overflow-auto p-7"
      :class="route.params.id ? '' : 'hidden sm:block'"
    >
      <RouterLink to="/inspector" class="sm:hidden inline-block mb-4 text-dim text-[13px] no-underline">
        ← all memories
      </RouterLink>
      <div v-if="!detail && detailError" class="text-ember h-full flex items-center justify-center">
        couldn't load this memory — {{ detailError }}
      </div>
      <div v-else-if="!detail" class="text-dim h-full flex items-center justify-center">
        Select a memory to see its provenance.
      </div>
      <div v-else class="max-w-[640px]">
        <div class="text-[20px] text-fg leading-snug mb-3">{{ detail.content }}</div>
        <div class="text-[12px] text-dim mb-5">
          {{ detail.memory_type }} · {{ detail.status }} · S={{ detail.strength.value }} ({{ detail.strength.band }})
          · confidence {{ detail.confidence }}
          <span v-if="detail.strength.at_risk" class="text-ember ml-1">· ❄ at risk</span>
        </div>

        <!-- write actions (CSRF-guarded). Forget archives (revivable); Correct supersedes. -->
        <div class="flex flex-wrap gap-2 mb-2">
          <button type="button" class="act" :disabled="busy" @click="act(() => api.pin(detail!.id))">
            ⬡ Pin
          </button>
          <button
            v-if="detail.status === 'active'"
            type="button"
            class="act"
            :disabled="busy"
            @click="act(() => api.forget(detail!.id))"
          >
            Forget
          </button>
          <button v-else type="button" class="act" :disabled="busy" @click="act(() => api.revive(detail!.id))">
            Revive
          </button>
          <button type="button" class="act" :disabled="busy" @click="correcting = !correcting">
            Correct…
          </button>
        </div>
        <div v-if="correcting" class="flex gap-2 mb-2">
          <input
            v-model="correctText"
            placeholder="the corrected fact…"
            class="flex-1 bg-panel border border-line rounded-[8px] px-3 py-1.5 text-fg text-[13px] outline-none focus:border-dim"
            @keyup.enter="submitCorrect"
          />
          <button type="button" class="act" :disabled="busy || !correctText.trim()" @click="submitCorrect">
            Save
          </button>
        </div>
        <p v-if="detailError" class="text-ember text-[12px] mb-4" role="alert">{{ detailError }}</p>
        <div class="mb-6" />

        <div class="text-[12px] tracking-wide text-dim mb-2">PROVENANCE</div>
        <div v-for="(s, i) in detail.sources" :key="i" class="text-[13px] text-fg/90 mb-1">
          {{ s.kind }} · {{ s.ref }}<span v-if="s.role"> · {{ s.role }}</span>
          <span class="text-dim"> · {{ s.observed_at.slice(0, 10) }}</span>
        </div>
        <p v-if="!detail.sources.length" class="text-dim text-[13px]">—</p>

        <template v-if="detail.edges.length">
          <div class="text-[12px] tracking-wide text-dim mb-2 mt-6">EDGES</div>
          <RouterLink
            v-for="(e, i) in detail.edges"
            :key="i"
            :to="`/fact/${e.dst}`"
            class="flex items-baseline gap-2 text-[13px] mb-1 no-underline hover:underline"
            :class="edgeStyle(e.relation).cls"
          >
            <span class="w-4 text-center">{{ edgeStyle(e.relation).icon }}</span>
            <span>{{ e.relation }}</span>
            <span class="text-dim font-mono">{{ e.dst.slice(0, 8) }}</span>
          </RouterLink>
        </template>

        <!-- belief trail (ux-design §4.2): the rewindable supersession history of this fact -->
        <template v-if="history.length > 1">
          <div class="text-[12px] tracking-wide text-dim mb-2 mt-6">BELIEF HISTORY</div>
          <ol class="border-l border-line pl-4">
            <li v-for="(v, i) in history" :key="v.id" class="relative mb-2.5 text-[13px]">
              <span
                class="absolute -left-[21px] top-1.5 w-2 h-2 rounded-full"
                :class="v.status === 'active' ? 'bg-ember' : 'bg-dim'"
              />
              <span :class="v.status === 'active' ? 'text-fg' : 'text-dim line-through'">
                {{ v.content }}
              </span>
              <span class="text-dim text-[11px] ml-2 font-mono">
                v{{ v.version }}<template v-if="i < history.length - 1"> ⇒</template>
              </span>
            </li>
          </ol>
        </template>

        <!-- cross-surface footer (ux-design §6.5): the same memory in the CLI — one product, 3 lenses -->
        <div class="text-[11px] text-dim/70 mt-8 pt-4 border-t border-line font-mono">
          cli: cold-frame show {{ detail.id.slice(0, 8) }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.act {
  font-size: 12px;
  color: #8a8a93;
  padding: 5px 11px;
  border: 1px solid #1c1c22;
  border-radius: 8px;
  background: #101015;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.act:hover:not(:disabled) {
  color: #e7e7ea;
  border-color: #8a8a93;
}
.act:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
