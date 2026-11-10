import type { Codebook, ConversationMeta, ReconciledCodebook, SpeakerTurn, TranscriptIndex } from './types'

// Globs rather than static imports, so a missing file (bottom-up not generated yet,
// transcripts not exported) renders an empty state instead of failing the build.
const derived = import.meta.glob<Codebook>('../../../data/derived/*.json', {
  eager: true,
  import: 'default',
})
const indexFiles = import.meta.glob<TranscriptIndex>('../../../data/transcripts/index.json', {
  eager: true,
  import: 'default',
})
const transcriptLoaders = import.meta.glob<SpeakerTurn[]>('../../../data/transcripts/conv-*.json', {
  import: 'default',
})

// Lazy, so the large saved reconciled codebook is only fetched when browser storage is empty.
const savedReconciled = import.meta.glob<ReconciledCodebook>('../../../data/reconciled/reconciled_codebook.json', {
  import: 'default',
})

/** The reconciled codebook saved in the repo, or null if there is none. */
export function loadRepoReconciled(): Promise<ReconciledCodebook | null> {
  const load = savedReconciled['../../../data/reconciled/reconciled_codebook.json']
  return load ? load() : Promise.resolve(null)
}

export function loadCodebook(
  name: 'bottom_up_codebook' | 'bottom_up_legacy_codebook' | 'top_down_codebook',
): Codebook | null {
  return derived[`../../../data/derived/${name}.json`] ?? null
}

export const transcriptIndex: TranscriptIndex | null =
  indexFiles['../../../data/transcripts/index.json'] ?? null

export const conversationsById: Map<number, ConversationMeta> = new Map(
  (transcriptIndex?.conversations ?? []).map((c) => [c.conversation_id, c]),
)

let allTurns: Promise<SpeakerTurn[]> | null = null

export function loadAllTurns(): Promise<SpeakerTurn[]> {
  if (!allTurns) {
    allTurns = Promise.all(Object.values(transcriptLoaders).map((load) => load())).then((files) =>
      files.flat(),
    )
  }
  return allTurns
}
