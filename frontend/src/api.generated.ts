/* eslint-disable */
// GENERATED from cold_frame/ui/contract.py — DO NOT EDIT.
// Regenerate with `pnpm run gen:types`.

/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "Band".
 */
export type Band = 'evergreen' | 'budding' | 'fading'
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "MemoryType".
 */
export type MemoryType = 'semantic' | 'episodic' | 'procedural'
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "Status".
 */
export type Status = 'active' | 'archived' | 'deleted'
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "TriageReason".
 */
export type TriageReason = 'true_conflict' | 'ambiguous_merge' | 'low_confidence' | 'pin_adjacent_archive'

export interface ColdframeApiContract {
  [k: string]: unknown
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "Edge".
 */
export interface Edge {
  src: string
  dst: string
  relation: string
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "FactDetail".
 */
export interface FactDetail {
  id: string
  content: string
  memory_type: MemoryType
  status: Status
  confidence: number
  strength: Strength
  sources: Source[]
  valid_at: string | null
  edges: Edge[]
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "Strength".
 */
export interface Strength {
  value: number
  band: Band
  at_risk: boolean
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "Source".
 */
export interface Source {
  kind: string
  ref: string
  role: string | null
  observed_at: string
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "FactHistoryResponse".
 */
export interface FactHistoryResponse {
  versions: HistoryVersion[]
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "HistoryVersion".
 */
export interface HistoryVersion {
  id: string
  content: string
  status: Status
  version: number
  valid_at: string | null
  invalid_at: string | null
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "FieldNote".
 */
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
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "MemoryFieldResponse".
 */
export interface MemoryFieldResponse {
  notes: FieldNote[]
  total: number
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "NoteBrief".
 */
export interface NoteBrief {
  id: string
  content: string
  memory_type: MemoryType
  status: Status
  confidence: number
  strength: Strength
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "NotesResponse".
 */
export interface NotesResponse {
  notes: NoteBrief[]
  total: number
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "SearchHit".
 */
export interface SearchHit {
  id: string
  content: string
  memory_type: MemoryType
  status: Status
  confidence: number
  strength: Strength
  score: number
  signals: Signals
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "Signals".
 */
export interface Signals {
  semantic: number | null
  bm25: number | null
  edge: number | null
  rrf: number
  rerank: number | null
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "SearchResponse".
 */
export interface SearchResponse {
  query: string
  hits: SearchHit[]
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "TriageItem".
 */
export interface TriageItem {
  id: string
  content: string
  memory_type: MemoryType
  status: Status
  confidence: number
  strength: Strength
  reason: TriageReason
  candidates: string[]
  impact: number
}
/**
 * This interface was referenced by `ColdframeApiContract`'s JSON-Schema
 * via the `definition` "TriageResponse".
 */
export interface TriageResponse {
  items: TriageItem[]
}
