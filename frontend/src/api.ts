// Typed client for the local read-only JSON API (cold_frame/ui/server.py). The TYPES are GENERATED
// from the Python wire contract (cold_frame/ui/contract.py → api.schema.json → api.generated.ts);
// run `pnpm run gen:types` after changing a server payload shape. The two languages cannot drift —
// the core test gate fails if the schema is stale, and CI re-runs the generator + git diff.
import type {
  FactDetail,
  FactHistoryResponse,
  MemoryFieldResponse,
  MemoryType,
  NoteBrief,
  NotesResponse,
  SearchResponse,
  TriageResponse,
} from './api.generated'

// re-export every generated type (no hand-maintained list → a new contract type is exported
// automatically; the only local export, `api`, can't collide with the generated interface names).
export * from './api.generated'

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!r.ok) throw new Error(`${url} → ${r.status}`)
  return r.json() as Promise<T>
}

// the per-process CSRF token the server injected into the page (security-spec §localhost).
const csrfToken = (): string =>
  document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') ?? ''

// mutating call: the browser supplies same-origin Origin automatically; we add the CSRF token header.
async function postJSON<T>(url: string, body: unknown = {}): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${url} → ${r.status}`)
  return r.json() as Promise<T>
}

const enc = encodeURIComponent

export const api = {
  memoryField: () => getJSON<MemoryFieldResponse>('/api/memory-field'),
  notes: () => getJSON<NotesResponse>('/api/notes'),
  fact: (id: string) => getJSON<FactDetail>(`/api/fact/${enc(id)}`),
  factHistory: (id: string) => getJSON<FactHistoryResponse>(`/api/fact/${enc(id)}/history`),
  search: (q: string) => getJSON<SearchResponse>(`/api/search?q=${enc(q)}`),
  triage: () => getJSON<TriageResponse>('/api/triage'),
  health: () => getJSON<Record<string, unknown>>('/api/health'),
  // mutations (CSRF-guarded)
  pin: (id: string) => postJSON<NoteBrief>(`/api/fact/${enc(id)}/pin`),
  forget: (id: string) => postJSON<NoteBrief>(`/api/fact/${enc(id)}/forget`),
  revive: (id: string) => postJSON<NoteBrief>(`/api/fact/${enc(id)}/revive`),
  correct: (id: string, text: string) => postJSON<NoteBrief>(`/api/fact/${enc(id)}/correct`, { text }),
  create: (text: string, memory_type: MemoryType = 'semantic') =>
    postJSON<{ added: NoteBrief | null; deduped: string[] }>('/api/fact', { text, memory_type }),
  resolveTriage: (id: string, action: string, target?: string) =>
    postJSON<{ ok: boolean }>(`/api/triage/${enc(id)}/resolve`, { action, target }),
}
