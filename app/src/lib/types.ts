export interface SpeakerTurn {
  snippet_id: number
  snippet_cortico_id: number | null
  conversation_id: number
  speaker_id: number | null
  speaker_name: string | null
  is_facilitator: boolean
  text: string
  index_in_conversation: number
  audio_start_offset: number
  audio_end_offset: number
}

export interface EvidenceTurn extends SpeakerTurn {
  span: { start_index: number; end_index: number }
  span_text: string
  evidence_source: string
}

export interface Entry {
  connection_id: number | null
  conversation_id: number
  insight_text: string
  evidence: EvidenceTurn[]
}

export type CodeSource = 'bottom_up' | 'top_down' | 'reconciled'
export type PanelId = 'bottom_up' | 'top_down' | 'reconciled'

export interface RQRef {
  id: number
  name: string
}

export interface Code {
  id: string
  name: string
  definition: string
  source: CodeSource
  rq_provenance?: RQRef
  highlights: Entry[]
  exemplars: Entry[]
  children?: Code[]
  /** true for codes the top-down pipeline generated (scripts/top_down_children.py) */
  generated?: boolean
  name_warnings?: string[]
  /** id of the bottom-up or top-down code this reconciled code was dragged from */
  origin_id?: string
}

export interface ResearchQuestion {
  id: number
  name: string
  description: string
  merged_from: number[]
  family_ids: string[]
}

export interface Codebook {
  type: 'bottom_up' | 'top_down'
  generated_at: string
  codes: Code[]
  research_questions?: ResearchQuestion[]
  warnings?: string[]
  placeholder?: boolean
  placeholder_note?: string
  coverage_note?: string
}

export interface ReconciledCodebook {
  version: number
  last_saved: string
  codes: Code[]
}

export interface ConversationMeta {
  conversation_id: number
  cortico_id: number | null
  project_id: number | null
  title: string | null
  duration_seconds: number | null
  turn_count: number
  facilitator_turn_count: number
  cited_by_rq_ids: number[]
  file: string
}

export interface TranscriptIndex {
  conversation_count: number
  turn_count: number
  conversations_without_transcript: number[]
  conversations: ConversationMeta[]
}
