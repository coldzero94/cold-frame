// Typed client for the local read-only JSON API (cold_frame/ui/server.py). The SHAPES here are
// the stable seam: the same /api/memory-field contract drives the prototype validation and the
// live MemoryField component, so a server-side shape change fails the TS build at the boundary.

export type Band = 'evergreen' | 'budding' | 'fading'

/** One note as the p5 MemoryField viz consumes it (GET /api/memory-field). */
export interface FieldNote {
  id: string
  content: string
  type: string
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
  memory_type: string
  status: string
  confidence: number
  strength: { value: number; band: string; at_risk: boolean }
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
