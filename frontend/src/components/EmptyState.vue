<script setup lang="ts">
// Cold-start / first-run guidance (ux-design §9.2). The UI is read-only, so the CTAs are copyable
// commands (add a memory · connect Claude Code) rather than write forms — it updates live once a
// memory lands. Used by both the empty Field and the empty Inspector.
import { ref } from 'vue'

const copied = ref('')
const ADD = 'cold-frame add "I prefer dark roast coffee"'
const MCP = 'claude mcp add cold-frame -- cold-frame mcp'

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = text
    setTimeout(() => (copied.value = ''), 1500)
  } catch {
    /* clipboard blocked (no secure context) — the command is still visible to copy by hand */
  }
}
</script>

<template>
  <div class="max-w-md text-center px-6">
    <div class="text-[32px] mb-3 opacity-50" aria-hidden="true">🌱</div>
    <h2 class="text-fg text-[17px] mb-2">Nothing planted yet</h2>
    <p class="text-dim text-[13px] leading-relaxed mb-6">
      Coldframe remembers facts about you — and lets your agent recall them. Add your first memory
      and it appears here as a warm ember; the cold is forgetting.
    </p>

    <div class="text-left flex flex-col gap-4">
      <div>
        <div class="text-dim text-[11px] mb-1.5 tracking-[0.08em]">ADD A MEMORY</div>
        <button
          class="snippet"
          :aria-label="`Copy: ${ADD}`"
          @click="copy(ADD)"
        >
          <code>{{ ADD }}</code>
          <span class="hint">{{ copied === ADD ? 'copied ✓' : 'copy' }}</span>
        </button>
      </div>
      <div>
        <div class="text-dim text-[11px] mb-1.5 tracking-[0.08em]">CONNECT CLAUDE CODE</div>
        <button
          class="snippet"
          :aria-label="`Copy: ${MCP}`"
          @click="copy(MCP)"
        >
          <code>{{ MCP }}</code>
          <span class="hint">{{ copied === MCP ? 'copied ✓' : 'copy' }}</span>
        </button>
      </div>
    </div>

    <p class="text-dim/70 text-[11px] mt-6">This view updates live as memories are added.</p>
  </div>
</template>

<style scoped>
.snippet {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #1c1c22;
  border-radius: 8px;
  background: #101015;
  cursor: pointer;
  transition: border-color 0.15s;
}
.snippet:hover {
  border-color: #8a8a93;
}
.snippet code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #e7e7ea;
  text-align: left;
  word-break: break-all;
}
.snippet .hint {
  flex-shrink: 0;
  font-size: 11px;
  color: #8a8a93;
}
</style>
