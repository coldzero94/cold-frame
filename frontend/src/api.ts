// Typed client for the local read-only JSON API (cold_frame/ui/server.py). The TYPES are GENERATED
// from the Python wire contract (cold_frame/ui/contract.py → api.schema.json → api.generated.ts);
// run `pnpm run gen:types` after changing a server payload shape. The two languages cannot drift —
// the core test gate fails if the schema is stale, and CI re-runs the generator + git diff.
import type {
  CreateFactResponse,
  FactDetail,
  FactHistoryResponse,
  MemoryFieldResponse,
  MemoryType,
  NoteBrief,
  NotesResponse,
  SearchResponse,
  TriageResolveResponse,
  TriageResponse,
} from './api.generated'

// re-export every generated type (no hand-maintained list → a new contract type is exported
// automatically; the only local export, `api`, can't collide with the generated interface names).
export * from './api.generated'

// Human messages for the server's stable error codes (so a deliberate refusal — e.g. a blocked
// secret — reads as itself, not a generic "Failed to fetch" from a dropped/!ok response).
const ERR_MESSAGE: Record<string, string> = {
  secret_blocked: "That looked like a secret, so it wasn't stored.",
  text_required: 'Please enter some text.',
  not_found: 'That memory no longer exists.',
  bad_memory_type: 'Unknown memory type.',
  bad_action: 'Unknown action.',
  csrf_failed: 'Security check failed — reload the page and try again.',
  internal: 'Something went wrong on the server.',
}

async function apiError(url: string, r: Response): Promise<Error> {
  let code = ''
  try {
    code = ((await r.json()) as { error?: string })?.error ?? ''
  } catch {
    /* non-JSON body — fall back to the status line */
  }
  return new Error(ERR_MESSAGE[code] ?? `${url} → ${r.status}`)
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!r.ok) throw await apiError(url, r)
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
  if (!r.ok) throw await apiError(url, r)
  return r.json() as Promise<T>
}

const enc = encodeURIComponent

export const api = {
  memoryField: () => getJSON<MemoryFieldResponse>('/api/memory-field'),
  notes: () => getJSON<NotesResponse>('/api/notes'),
  fact: (id: string) => getJSON<FactDetail>(`/api/fact/${enc(id)}`),
  factHistory: (id: string) => getJSON<FactHistoryResponse>(`/api/fact/${enc(id)}/history`),
  search: (q: string, asOf?: string) =>
    getJSON<SearchResponse>(`/api/search?q=${enc(q)}${asOf ? `&as_of=${enc(asOf)}` : ''}`),
  triage: () => getJSON<TriageResponse>('/api/triage'),
  health: () => getJSON<Record<string, unknown>>('/api/health'),
  // mutations (CSRF-guarded)
  pin: (id: string) => postJSON<NoteBrief>(`/api/fact/${enc(id)}/pin`),
  forget: (id: string) => postJSON<NoteBrief>(`/api/fact/${enc(id)}/forget`),
  revive: (id: string) => postJSON<NoteBrief>(`/api/fact/${enc(id)}/revive`),
  correct: (id: string, text: string) => postJSON<NoteBrief>(`/api/fact/${enc(id)}/correct`, { text }),
  create: (text: string, memory_type: MemoryType = 'semantic') =>
    postJSON<CreateFactResponse>('/api/fact', { text, memory_type }),
  resolveTriage: (id: string, action: string, target?: string) =>
    postJSON<TriageResolveResponse>(`/api/triage/${enc(id)}/resolve`, { action, target }),
}
