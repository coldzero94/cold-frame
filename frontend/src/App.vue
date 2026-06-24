<script setup lang="ts">
import { appError } from '@/appError'

const nav = [
  { to: '/', label: 'Field', glyph: '◎' },
  { to: '/inspector', label: 'Inspector', glyph: '☰' },
  { to: '/triage', label: 'Triage', glyph: '⬡' },
  { to: '/search', label: 'Search', glyph: '⌕' },
]

function dismiss(): void {
  appError.value = ''
}
</script>

<template>
  <div class="flex h-screen bg-ink text-fg font-sans">
    <nav class="w-[220px] flex-shrink-0 border-r border-line p-5 flex flex-col gap-1">
      <div class="text-[13px] tracking-[0.06em] text-dim font-600 mb-7">COLDFRAME</div>
      <RouterLink
        v-for="n in nav"
        :key="n.to"
        :to="n.to"
        class="flex items-center gap-3 px-3 py-[9px] rounded-[8px] text-dim no-underline transition-colors hover:bg-panel hover:text-fg"
        active-class="bg-panel !text-fg"
      >
        <span class="w-5 text-center opacity-80">{{ n.glyph }}</span>{{ n.label }}
      </RouterLink>
      <div class="mt-auto text-[11px] text-dim leading-relaxed">
        your memory, local-first.<br />warmth is belief; the cold is forgetting.
      </div>
    </nav>
    <main class="flex-1 min-w-0 overflow-auto">
      <RouterView />
    </main>

    <!-- catch-all error banner (operational; dismissible) — wired in main.ts -->
    <div
      v-if="appError"
      role="alert"
      class="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 max-w-[80%] flex items-center gap-3 rounded-[10px] border border-ember/50 bg-panel px-4 py-2.5 text-[13px] shadow-lg"
    >
      <span class="text-ember">⚠</span>
      <span class="text-fg truncate">{{ appError }}</span>
      <button class="text-dim hover:text-fg ml-1" title="Dismiss" @click="dismiss">✕</button>
    </div>
  </div>
</template>
