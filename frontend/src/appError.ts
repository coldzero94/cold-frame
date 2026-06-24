import { ref } from 'vue'

// A single app-wide error surface: the catch-all handlers (main.ts) write here and App.vue shows
// a dismissible banner. Operational errors — a crashed component, an unhandled promise rejection,
// a script error (e.g. the vendored p5 failing to load) — become VISIBLE instead of dying in the
// console. Distinct from belief-change toasts (those are the UX spec's "only when a belief changes").
export const appError = ref('')

export function reportError(e: unknown): void {
  const msg = e instanceof Error ? e.message : e ? String(e) : ''
  if (msg) appError.value = msg // keep falsy/empty noise (benign resource events) off the banner
}
