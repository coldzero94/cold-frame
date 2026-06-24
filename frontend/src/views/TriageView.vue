<script setup lang="ts">
// The human-resolution queue: memories held for review (low-confidence / conflict / ambiguous-merge).
// v1 offers the targetless resolutions (Keep · Pin · Let go); merge/supersede need a target-picker.
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, type TriageItem } from '@/api'

const router = useRouter()
const items = ref<TriageItem[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')

const REASON: Record<string, string> = {
  true_conflict: 'conflicts with another memory',
  ambiguous_merge: 'might be a duplicate',
  low_confidence: 'low confidence',
  pin_adjacent_archive: 'near a pinned memory',
}

async function load(): Promise<void> {
  loading.value = true
  try {
    items.value = (await api.triage()).items
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

async function resolve(id: string, action: string): Promise<void> {
  busy.value = id
  error.value = ''
  try {
    await api.resolveTriage(id, action)
    items.value = items.value.filter((i) => i.id !== id) // drop the resolved item
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <div class="h-full overflow-auto p-4 sm:p-6 max-w-[720px]">
    <div class="text-[12px] tracking-wide text-dim mb-4">TRIAGE — HELD FOR YOUR REVIEW</div>

    <p v-if="loading" class="text-dim text-[13px]">loading…</p>
    <p v-else-if="error" class="text-ember text-[13px]" role="alert">{{ error }}</p>
    <div v-else-if="!items.length" class="text-dim text-[13px]">
      Nothing to triage — every memory has cleared the durability gate. ✓
    </div>

    <div
      v-for="it in items"
      :key="it.id"
      class="rounded-[10px] p-3.5 mb-2.5 border border-line bg-panel"
    >
      <button
        type="button"
        class="block w-full text-left text-fg text-[14px] leading-snug mb-1 bg-transparent border-0 p-0 cursor-pointer"
        @click="router.push(`/fact/${it.id}`)"
      >
        {{ it.content }}
      </button>
      <div class="text-[11px] text-dim mb-3">
        {{ it.memory_type }} · held: {{ REASON[it.reason] ?? it.reason }}
      </div>
      <div class="flex gap-2 flex-wrap">
        <button class="t-act" :disabled="busy === it.id" @click="resolve(it.id, 'keep')">Keep</button>
        <button class="t-act" :disabled="busy === it.id" @click="resolve(it.id, 'pin')">⬡ Pin</button>
        <button class="t-act muted" :disabled="busy === it.id" @click="resolve(it.id, 'let_go')">
          Let go
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.t-act {
  font-size: 12px;
  color: #8a8a93;
  padding: 5px 12px;
  border: 1px solid #1c1c22;
  border-radius: 8px;
  background: #0b0b0f;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.t-act:hover:not(:disabled) {
  color: #e7e7ea;
  border-color: #8a8a93;
}
.t-act.muted:hover:not(:disabled) {
  color: #d97757;
  border-color: #d97757;
}
.t-act:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
