import { useEffect, useMemo, useState } from 'react'
import { conversationsById, loadAllTurns } from '../lib/data'
import { entryKey, turnToEntry } from '../lib/reconciled'
import type { Entry, SpeakerTurn } from '../lib/types'
import { HighlightCard } from './HighlightCard'

interface Props {
  pinMode: boolean
  checkedKeys: Set<string>
  onToggle: (entry: Entry) => void
}

type Mode = 'meaning' | 'keyword'

interface Hit {
  turn: SpeakerTurn
  label: string
}

const STOP = new Set(['the', 'and', 'for', 'that', 'with', 'are', 'was', 'you', 'they', 'this', 'have', 'but', 'not'])
const MAX_RESULTS = 50
// Ask the server for more than we show, so hiding facilitator turns still leaves a full page.
const SERVER_LIMIT = 300
const SERVER_START = '.venv/bin/python scripts/search_server.py'

function terms(q: string): string[] {
  return [...new Set(q.toLowerCase().match(/[a-z0-9']+/g) ?? [])].filter((w) => w.length > 2 && !STOP.has(w))
}

/** Share of query terms present, weighted by frequency and turn length. */
function keywordScore(turn: SpeakerTurn, qs: string[]): { score: number; matched: number } {
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

type Ranked = { snippet_id: number; similarity: number }[]
const similarityCache = new Map<string, Ranked>()

async function similaritySearch(query: string): Promise<Ranked> {
  const key = query.trim().toLowerCase().replace(/\s+/g, ' ')
  const cached = similarityCache.get(key)
  if (cached) return cached
  let res: Response
  try {
    res = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, limit: SERVER_LIMIT }),
    })
  } catch {
    throw new Error('offline')
  }
  // Vite's proxy answers 500/502/504 with a non-JSON body when the search server isn't running.
  const body = await res.json().catch(() => null)
  if (!res.ok || !body) throw new Error(body?.detail ?? 'offline')
  similarityCache.set(key, body.results)
  return body.results
}

export function SearchPanel({ pinMode, checkedKeys, onToggle }: Props) {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<Mode>('meaning')
  const [hideFacilitator, setHideFacilitator] = useState(true)
  const [turns, setTurns] = useState<SpeakerTurn[] | null>(null)
  const [submitted, setSubmitted] = useState<{ query: string; mode: Mode } | null>(null)
  const [ranked, setRanked] = useState<Ranked | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!submitted) return
    let stale = false
    const go = async () => {
      setBusy(true)
      setError(null)
      try {
        const all = turns ?? (await loadAllTurns())
        if (!turns && !stale) setTurns(all)
        const r = submitted.mode === 'meaning' ? await similaritySearch(submitted.query) : null
        if (!stale) setRanked(r)
      } catch (e) {
        if (!stale) {
          setRanked(null)
          setError((e as Error).message)
        }
      } finally {
        if (!stale) setBusy(false)
      }
    }
    void go()
    return () => {
      stale = true
    }
    // turns is filled by this effect; re-running on it would search twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submitted])

  const turnsById = useMemo(() => new Map((turns ?? []).map((t) => [t.snippet_id, t])), [turns])

  const hits: Hit[] = useMemo(() => {
    if (!submitted || !turns) return []
    const keep = (t: SpeakerTurn) => !(hideFacilitator && t.is_facilitator)
    if (submitted.mode === 'meaning') {
      if (!ranked) return []
      const out: Hit[] = []
      for (const r of ranked) {
        const t = turnsById.get(r.snippet_id)
        if (!t || !keep(t)) continue
        out.push({ turn: t, label: `similarity ${r.similarity.toFixed(2)}` })
        if (out.length >= MAX_RESULTS) break
      }
      return out
    }
    const qs = terms(submitted.query)
    if (qs.length === 0) return []
    const scored: { turn: SpeakerTurn; score: number; matched: number }[] = []
    for (const t of turns) {
      if (!keep(t)) continue
      const s = keywordScore(t, qs)
      if (s.score > 0) scored.push({ turn: t, ...s })
    }
    return scored
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_RESULTS)
      .map(({ turn, score, matched }) => ({
        turn,
        label: `score ${score.toFixed(2)} · ${matched} term${matched === 1 ? '' : 's'}`,
      }))
  }, [submitted, turns, ranked, turnsById, hideFacilitator])

  const submit = (m: Mode) => {
    const q = query.trim()
    if (q) setSubmitted({ query: q, mode: m })
  }

  return (
    <section className="search">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          submit(mode)
        }}
      >
        <input
          type="search"
          placeholder={
            mode === 'meaning'
              ? `Describe what you're looking for across ${conversationsById.size} conversations`
              : `Search ${conversationsById.size} conversations by keyword`
          }
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit" disabled={busy}>
          Search
        </button>
      </form>
      <div className="search-opts">
        <span className="mode" role="radiogroup" aria-label="Search by">
          {(['meaning', 'keyword'] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={mode === m}
              className={mode === m ? 'on' : ''}
              title={
                m === 'meaning'
                  ? 'Find turns similar in meaning (embedding similarity; each new search is a tiny paid OpenAI call)'
                  : 'Find turns containing your words'
              }
              onClick={() => {
                setMode(m)
                if (submitted && submitted.query === query.trim()) submit(m)
              }}
            >
              {m === 'meaning' ? 'Meaning' : 'Keyword'}
            </button>
          ))}
        </span>
        <label className="check">
          <input type="checkbox" checked={hideFacilitator} onChange={(e) => setHideFacilitator(e.target.checked)} />
          Hide facilitator turns
        </label>
      </div>
      {pinMode && <p className="note">Tick results to pin them as exemplars.</p>}
      <div className="results">
        {busy && <p className="empty">{turns ? 'Searching…' : 'Loading transcripts…'}</p>}
        {!busy && error && (
          <div className="empty search-error">
            {error === 'offline' ? (
              <>
                <p>The similarity search server isn't running. Start it from the Codestormer folder:</p>
                <code>{SERVER_START}</code>
              </>
            ) : (
              <p>Similarity search failed: {error}</p>
            )}
            <button type="button" onClick={() => (setMode('keyword'), submit('keyword'))}>
              Search by keyword instead
            </button>
          </div>
        )}
        {!busy && !error && submitted && turns && hits.length === 0 && <p className="empty">No matches.</p>}
        {hits.map(({ turn, label }) => {
          const entry = turnToEntry(turn)
          return (
            <HighlightCard
              key={turn.snippet_id}
              entry={entry}
              label={label}
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
