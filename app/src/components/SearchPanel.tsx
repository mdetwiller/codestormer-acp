import { useMemo, useState } from 'react'
import { conversationsById, loadAllTurns } from '../lib/data'
import { entryKey, turnToEntry } from '../lib/reconciled'
import type { Entry, SpeakerTurn } from '../lib/types'
import { HighlightCard } from './HighlightCard'

interface Props {
  pinMode: boolean
  checkedKeys: Set<string>
  onToggle: (entry: Entry) => void
}

interface Hit {
  turn: SpeakerTurn
  score: number
  matched: number
}

const STOP = new Set(['the', 'and', 'for', 'that', 'with', 'are', 'was', 'you', 'they', 'this', 'have', 'but', 'not'])
const MAX_RESULTS = 50

function terms(q: string): string[] {
  return [...new Set(q.toLowerCase().match(/[a-z0-9']+/g) ?? [])].filter((w) => w.length > 2 && !STOP.has(w))
}

/** Keyword stand-in for semantic search: share of query terms present, weighted by frequency and turn length. */
function score(turn: SpeakerTurn, qs: string[]): { score: number; matched: number } {
  const text = turn.text.toLowerCase()
  let hits = 0
  let matched = 0
  for (const q of qs) {
    const n = text.split(q).length - 1
    if (n > 0) {
      matched++
      hits += n
    }
  }
  if (!matched) return { score: 0, matched: 0 }
  const length = Math.max(20, text.split(/\s+/).length)
  return { score: (matched / qs.length) * (1 + Math.log1p(hits) / Math.log(length)), matched }
}


export function SearchPanel({ pinMode, checkedKeys, onToggle }: Props) {
  const [query, setQuery] = useState('')
  const [hideFacilitator, setHideFacilitator] = useState(true)
  const [turns, setTurns] = useState<SpeakerTurn[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [submitted, setSubmitted] = useState('')

  const run = async () => {
    setSubmitted(query)
    if (!turns && !loading) {
      setLoading(true)
      setTurns(await loadAllTurns())
      setLoading(false)
    }
  }

  const hits: Hit[] = useMemo(() => {
    const qs = terms(submitted)
    if (!turns || qs.length === 0) return []
    const out: Hit[] = []
    for (const t of turns) {
      if (hideFacilitator && t.is_facilitator) continue
      const s = score(t, qs)
      if (s.score > 0) out.push({ turn: t, ...s })
    }
    return out.sort((a, b) => b.score - a.score).slice(0, MAX_RESULTS)
  }, [turns, submitted, hideFacilitator])

  const total = conversationsById.size

  return (
    <section className="search">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void run()
        }}
      >
        <input
          type="search"
          placeholder={`Search ${total} conversations by keyword`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit">Search</button>
      </form>
      <label className="check">
        <input type="checkbox" checked={hideFacilitator} onChange={(e) => setHideFacilitator(e.target.checked)} />
        Hide facilitator turns
      </label>
      <p className="note">
        Keyword stand-in for semantic search.{pinMode ? ' Tick results to pin them as exemplars.' : ''}
      </p>
      <div className="results">
        {loading && <p className="empty">Loading transcripts…</p>}
        {!loading && submitted && turns && hits.length === 0 && <p className="empty">No matches.</p>}
        {hits.map(({ turn, score, matched }) => {
          const entry = turnToEntry(turn)
          const key = `${turn.snippet_id}`
          return (
            <HighlightCard
              key={key}
              entry={entry}
              label={`score ${score.toFixed(2)} · ${matched} term${matched === 1 ? '' : 's'}`}
              selectable={pinMode}
              checked={checkedKeys.has(entryKey(entry))}
              onToggle={() => onToggle(entry)}
            />
          )
        })}
      </div>
    </section>
  )
}
