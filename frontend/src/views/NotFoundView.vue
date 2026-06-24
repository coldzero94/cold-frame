<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <div class="h-full flex items-center justify-center text-dim">
    <div class="text-center max-w-md px-6">
      <div class="text-[28px] mb-3 opacity-60">∅</div>
      <div class="text-fg mb-2">Nothing here</div>
      <p class="text-[13px] leading-relaxed">
        <code class="font-mono">{{ route.fullPath }}</code> isn't a page (the memory may have been
        forgotten, or the link is stale).
      </p>
      <RouterLink to="/" class="inline-block mt-4 text-ember no-underline hover:underline">
        ← back to the field
      </RouterLink>
    </div>
  </div>
</template>
