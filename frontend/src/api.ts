// Typed client for the local read-only JSON API (cold_frame/ui/server.py). The TYPES are GENERATED
// from the Python wire contract (cold_frame/ui/contract.py → api.schema.json → api.generated.ts);
// run `pnpm run gen:types` after changing a server payload shape. The two languages cannot drift —
// the core test gate fails if the schema is stale, and CI re-runs the generator + git diff.
import type {
  FactDetail,
  FactHistoryResponse,
  MemoryFieldResponse,
  NotesResponse,
  SearchResponse,
} from './api.generated'

// re-export every generated type (no hand-maintained list → a new contract type is exported
// automatically; the only local export, `api`, can't collide with the generated interface names).
export * from './api.generated'

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!r.ok) throw new Error(`${url} → ${r.status}`)
  return r.json() as Promise<T>
}

export const api = {
  memoryField: () => getJSON<MemoryFieldResponse>('/api/memory-field'),
  notes: () => getJSON<NotesResponse>('/api/notes'),
  fact: (id: string) => getJSON<FactDetail>(`/api/fact/${encodeURIComponent(id)}`),
  factHistory: (id: string) =>
    getJSON<FactHistoryResponse>(`/api/fact/${encodeURIComponent(id)}/history`),
  search: (q: string) => getJSON<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}`),
  health: () => getJSON<Record<string, unknown>>('/api/health'),
}
