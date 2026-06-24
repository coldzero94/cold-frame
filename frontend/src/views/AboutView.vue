<script setup lang="ts">
// Orientation + a health/doctor snapshot (the in-browser "what is this / is it healthy" surface).
import { onMounted, ref } from 'vue'
import { api } from '@/api'

const health = ref<Record<string, unknown>>({})
const error = ref('')
const copied = ref(false)
const MCP = 'claude mcp add cold-frame -- cold-frame mcp'

onMounted(async () => {
  try {
    health.value = await api.health()
  } catch (e) {
    error.value = String(e)
  }
})

async function copyMcp(): Promise<void> {
  try {
    await navigator.clipboard.writeText(MCP)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    /* clipboard blocked — the command is still readable */
  }
}
</script>

<template>
  <div class="h-full overflow-auto p-6 sm:p-8 max-w-[640px]">
    <h1 class="text-fg text-[18px] mb-2">Coldframe</h1>
    <p class="text-dim text-[13px] leading-relaxed mb-6">
      A local-first memory for you and your AI agent — one file at
      <code class="font-mono text-fg/80">~/.cold-frame/memory.db</code>. The
      <strong class="text-fg/90">Field</strong> is the live state of what's remembered (warmth =
      belief, cold = forgetting); the <strong class="text-fg/90">Inspector</strong> shows each fact's
      provenance and belief history; <strong class="text-fg/90">Search</strong> is hybrid recall;
      <strong class="text-fg/90">Triage</strong> is where held memories await your decision.
    </p>

    <div class="text-[12px] tracking-wide text-dim mb-2">CONNECT CLAUDE CODE</div>
    <button
      type="button"
      class="flex items-center justify-between gap-3 w-full rounded-[8px] border border-line bg-panel px-3 py-2.5 mb-6 cursor-pointer hover:border-dim transition-colors"
      :aria-label="`Copy: ${MCP}`"
      @click="copyMcp"
    >
      <code class="font-mono text-[12px] text-fg break-all text-left">{{ MCP }}</code>
      <span class="text-dim text-[11px] shrink-0">{{ copied ? 'copied ✓' : 'copy' }}</span>
    </button>

    <div class="text-[12px] tracking-wide text-dim mb-2">HEALTH</div>
    <p v-if="error" class="text-ember text-[13px]">{{ error }}</p>
    <table v-else class="text-[12px] w-full">
      <tbody>
        <tr v-for="(v, kk) in health" :key="kk" class="border-b border-line/60">
          <td class="py-1.5 pr-4 text-dim font-mono align-top">{{ kk }}</td>
          <td class="py-1.5 text-fg/90 font-mono break-all">{{ v }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
