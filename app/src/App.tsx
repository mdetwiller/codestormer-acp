import { useEffect, useMemo, useReducer, useState } from 'react'
import './App.css'
import { CodeTree, type DragPayload } from './components/CodeTree'
import { HighlightsPanel } from './components/HighlightsPanel'
import { ReconciledPanel } from './components/ReconciledPanel'
import { SearchPanel } from './components/SearchPanel'
import { loadCodebook, transcriptIndex } from './lib/data'
import {
  entryKey,
  findCode,
  loadSaved,
  originIds,
  reducer,
  save,
  toCorticoExport,
  toReconciledFile,
} from './lib/reconciled'
import type { Entry, PanelId } from './lib/types'

const bottomUp = loadCodebook('bottom_up_codebook')
const topDown = loadCodebook('top_down_codebook')

function download(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

export default function App() {
  const [reconciled, dispatch] = useReducer(reducer, undefined, loadSaved)
  const [selected, setSelected] = useState<{ panel: PanelId; id: string } | null>(null)
  const [pinTargetId, setPinTargetId] = useState<string | null>(null)
  const [checked, setChecked] = useState<Map<string, Entry>>(new Map())
  const [confirmClear, setConfirmClear] = useState(false)

  useEffect(() => save(reconciled), [reconciled])

  const codesFor = (panel: PanelId) =>
    panel === 'bottom_up' ? (bottomUp?.codes ?? []) : panel === 'top_down' ? (topDown?.codes ?? []) : reconciled

  const selectedCode = selected ? findCode(codesFor(selected.panel), selected.id) : null
  const pinTarget = pinTargetId ? findCode(reconciled, pinTargetId) : null
  const placed = useMemo(() => originIds(reconciled), [reconciled])

  const toggle = (entry: Entry) =>
    setChecked((prev) => {
      const next = new Map(prev)
      const key = entryKey(entry)
      if (next.has(key)) next.delete(key)
      else next.set(key, entry)
      return next
    })

  const stopPin = () => {
    setPinTargetId(null)
    setChecked(new Map())
  }

  const onDropCode = (payload: DragPayload, parentId: string | null) => {
    if (payload.panel === 'reconciled') return
    const code = findCode(codesFor(payload.panel), payload.id)
    if (code) dispatch({ type: 'add', code, parentId })
  }

  const onMergeCode = (payload: DragPayload, targetId: string) => {
    const code = findCode(codesFor(payload.panel), payload.id)
    if (code) dispatch({ type: 'merge', code, targetId })
  }

  const emptyRqs = (topDown?.research_questions ?? []).filter((rq) => rq.family_ids.length === 0)

  return (
    <div className="app">
      <header className="topbar">
        <h1>Codestormer</h1>
        <span className="muted">
          {transcriptIndex
            ? `${transcriptIndex.conversation_count} conversations · ${transcriptIndex.turn_count.toLocaleString()} turns`
            : 'Transcripts not exported: run scripts/export_transcripts.py'}
        </span>
        <span className="spacer" />
        <button onClick={() => download('reconciled_codebook.json', toReconciledFile(reconciled))}>
          Export reconciled JSON
        </button>
        <button onClick={() => download('reconciled_codebook.cortico.json', toCorticoExport(reconciled))}>
          Export Cortico format
        </button>
        <button
          className={confirmClear ? 'danger' : ''}
          disabled={reconciled.length === 0}
          onBlur={() => setConfirmClear(false)}
          onClick={() => {
            if (!confirmClear) return setConfirmClear(true)
            dispatch({ type: 'clear' })
            stopPin()
            setConfirmClear(false)
          }}
        >
          {confirmClear ? 'Click again to clear' : 'Clear'}
        </button>
      </header>

      <main className="columns">
        <section className="panel bottom-up">
          <h2>
            Bottom-up <span className="muted">topic-driven</span>
          </h2>
          {bottomUp?.placeholder && (
            <p className="banner" title={bottomUp.placeholder_note}>
              Placeholder: hand-drafted from 7 conversations until the LLM pass runs.
            </p>
          )}
          {bottomUp?.coverage_note && <p className="banner">{bottomUp.coverage_note}</p>}
          {bottomUp ? (
            <CodeTree
              codes={bottomUp.codes}
              panel="bottom_up"
              selectedId={selected?.panel === 'bottom_up' ? selected.id : null}
              onSelect={(id) => setSelected({ panel: 'bottom_up', id })}
              inReconciled={placed}
            />
          ) : (
            <p className="empty">No bottom_up_codebook.json in data/derived yet.</p>
          )}
        </section>

        <section className="panel reconciled">
          <h2>
            Reconciled <span className="muted">{reconciled.length} parent codes</span>
          </h2>
          <ReconciledPanel
            codes={reconciled}
            dispatch={dispatch}
            onDropCode={onDropCode}
            onMergeCode={onMergeCode}
            selectedId={selected?.panel === 'reconciled' ? selected.id : null}
            onSelect={(id) => setSelected({ panel: 'reconciled', id })}
            pinTargetId={pinTargetId}
            onStartPin={(id) => {
              setPinTargetId(id)
              setChecked(new Map())
              setSelected({ panel: 'reconciled', id })
            }}
          />
        </section>

        <section className="panel top-down">
          <h2>
            Top-down <span className="muted">RQ-driven</span>
          </h2>
          {topDown ? (
            <>
              <CodeTree
                codes={topDown.codes}
                panel="top_down"
                selectedId={selected?.panel === 'top_down' ? selected.id : null}
                onSelect={(id) => setSelected({ panel: 'top_down', id })}
                inReconciled={placed}
              />
              {emptyRqs.length > 0 && (
                <div className="empty-rqs">
                  <h3>RQs with no code families yet</h3>
                  {emptyRqs.map((rq) => (
                    <p key={rq.id}>
                      <span className="rq-badge">RQ {rq.id}</span> {rq.name}
                    </p>
                  ))}
                </div>
              )}
            </>
          ) : (
            <p className="empty">No top_down_codebook.json: run scripts/build_top_down.py.</p>
          )}
        </section>
      </main>

      <footer className="dock">
        <HighlightsPanel
          key={selected ? `${selected.panel}:${selected.id}` : 'none'}
          code={selectedCode}
          panel={selected?.panel ?? null}
          pinTarget={pinTarget}
          checkedKeys={new Set(checked.keys())}
          onToggle={toggle}
          onConfirmPin={() => {
            if (pinTargetId) dispatch({ type: 'pin', id: pinTargetId, entries: [...checked.values()] })
            stopPin()
          }}
          onCancelPin={stopPin}
          onUnpin={(id, index) => dispatch({ type: 'unpin', id, index })}
        />
        <SearchPanel pinMode={pinTarget !== null} checkedKeys={new Set(checked.keys())} onToggle={toggle} />
      </footer>
    </div>
  )
}
