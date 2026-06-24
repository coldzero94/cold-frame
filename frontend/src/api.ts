// Typed client for the local read-only JSON API (cold_frame/ui/server.py). These interfaces
// MIRROR the server's TypedDict payload shapes (NoteBriefDict / FieldNoteDict / FactDetailDict);
// a shape change the UI consumes fails the vue-tsc build at the read site. The unions below match
// the Python Literals (models.py Band / MemoryTypeLiteral / StatusLiteral) one-to-one.

export type Band = 'evergreen' | 'budding' | 'fading'
export type MemoryType = 'semantic' | 'episodic' | 'procedural'
export type Status = 'active' | 'archived' | 'deleted'

/** One note as the p5 MemoryField viz consumes it (GET /api/memory-field). */
export interface FieldNote {
  id: string
  content: string
  type: MemoryType
  s: number
  band: Band
  atRisk: boolean
  importance: number
  access: number
  pinned: boolean
  ageDays: number
}

/** A note brief in the Inspector list (GET /api/notes). */
export interface NoteBrief {
  id: string
  content: string
  memory_type: MemoryType
  status: Status
  confidence: number
  strength: { value: number; band: Band; at_risk: boolean }
}

/** Full fact detail with provenance + edges (GET /api/fact/{id}). */
export interface FactDetail extends NoteBrief {
  sources: { kind: string; ref: string; role: string | null; observed_at: string }[]
  valid_at: string | null
  edges: { src: string; dst: string; relation: string }[]
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!r.ok) throw new Error(`${url} → ${r.status}`)
  return r.json() as Promise<T>
}

export const api = {
  memoryField: () => getJSON<{ notes: FieldNote[] }>('/api/memory-field'),
  notes: () => getJSON<{ notes: NoteBrief[] }>('/api/notes'),
  fact: (id: string) => getJSON<FactDetail>(`/api/fact/${encodeURIComponent(id)}`),
}
