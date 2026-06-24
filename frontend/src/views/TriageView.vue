<script setup lang="ts">
// P4: the human-in-the-loop triage queue (held_for_human items + resolve actions). The engine
// surface (Memory.triage_queue / resolve_triage) already exists; this view + its mutating routes
// (with the CSRF-token + Origin allowlist the security contract requires) land in P4.
</script>

<template>
  <div class="h-full flex items-center justify-center text-dim">
    <div class="text-center max-w-md px-6">
      <div class="text-[28px] mb-3 opacity-60">⬡</div>
      <div class="text-fg mb-2">Triage</div>
      <p class="text-[13px] leading-relaxed">
        Review held memories (low-confidence, conflicts, ambiguous merges) and resolve them.
        Lands in P4 alongside the write-API security contract.
      </p>
    </div>
  </div>
</template>
