<script setup lang="ts">
import { appError } from '@/appError'

const nav = [
  { to: '/', label: 'Field', glyph: '◎' },
  { to: '/inspector', label: 'Inspector', glyph: '☰' },
  { to: '/triage', label: 'Triage', glyph: '⬡' },
  { to: '/search', label: 'Search', glyph: '⌕' },
  { to: '/about', label: 'About', glyph: 'ⓘ' },
]

function dismiss(): void {
  appError.value = ''
}
</script>

<template>
  <!-- mobile: a bottom tab bar (flex-col-reverse → nav under main); desktop: a left sidebar -->
  <div class="flex flex-col-reverse sm:flex-row h-screen bg-ink text-fg font-sans">
    <nav
      class="flex-shrink-0 flex border-line bg-ink
             flex-row justify-around border-t px-1 py-1
             sm:w-[220px] sm:flex-col sm:gap-1 sm:justify-start sm:border-t-0 sm:border-r sm:p-5"
    >
      <div class="hidden sm:block text-[13px] tracking-[0.06em] text-dim font-600 mb-7">COLDFRAME</div>
      <RouterLink
        v-for="n in nav"
        :key="n.to"
        :to="n.to"
        class="flex items-center no-underline text-dim transition-colors hover:text-fg
               flex-1 flex-col justify-center gap-0.5 px-2 py-2 text-[11px] rounded-[8px]
               sm:flex-row sm:flex-initial sm:justify-start sm:gap-3 sm:py-[9px] sm:text-[14px] sm:hover:bg-panel"
        active-class="text-fg! sm:bg-panel"
      >
        <span class="text-center opacity-80 sm:w-5">{{ n.glyph }}</span>{{ n.label }}
      </RouterLink>
      <div class="hidden sm:block mt-auto text-[11px] text-dim leading-relaxed">
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
