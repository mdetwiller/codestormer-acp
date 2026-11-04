import { useState } from 'react'
import { entryKey, mergedHighlights } from '../lib/reconciled'
import type { Code, Entry, PanelId } from '../lib/types'
import { HighlightCard } from './HighlightCard'

interface Props {
  code: Code | null
  panel: PanelId | null
  pinTarget: Code | null
  checkedKeys: Set<string>
  onToggle: (entry: Entry) => void
  onConfirmPin: () => void
  onCancelPin: () => void
  onUnpin: (codeId: string, index: number) => void
}

const PAGE = 30

const PANEL_LABEL: Record<PanelId, string> = {
  bottom_up: 'Bottom-up',
  top_down: 'Top-down',
  reconciled: 'Reconciled',
}

export function HighlightsPanel({
  code,
  panel,
  pinTarget,
  checkedKeys,
  onToggle,
  onConfirmPin,
  onCancelPin,
  onUnpin,
}: Props) {
  const [limit, setLimit] = useState(PAGE)
  const pinMode = pinTarget !== null
  const pinnedKeys = new Set((pinTarget?.exemplars ?? []).map(entryKey))

  // Interleave child codes so the first page samples every child, not just the first one.
  const fromChildren: { e: Entry; name: string }[] = []
  const kids = code?.children ?? []
  const longest = Math.max(0, ...kids.map((k) => k.highlights.length))
  for (let i = 0; i < longest; i++) {
    for (const k of kids) if (i < k.highlights.length) fromChildren.push({ e: k.highlights[i], name: k.name })
  }
  const childTotal = fromChildren.length
  const fromMerged = code ? mergedHighlights(code) : []
  const ownAndMerged = (code?.highlights.length ?? 0) + fromMerged.length
  const total = ownAndMerged + childTotal
  const shown = Math.min(total, limit)

  const card = (entry: Entry, label?: string) => {
    const key = entryKey(entry)
    const already = pinnedKeys.has(key)
    return (
      <HighlightCard
        key={`${label ?? ''}-${key}`}
        entry={entry}
        label={already ? `${label ? `${label} · ` : ''}already pinned` : label}
        selectable={pinMode && !already}
        checked={checkedKeys.has(key)}
        onToggle={() => onToggle(entry)}
      />
    )
  }

  return (
    <section className="highlights">
      {pinMode && (
        <div className="pin-banner">
          <span>
            Pinning exemplars to <strong>{pinTarget.name}</strong>. Tick highlights here or in search results;
            click other codes to browse their highlights.
          </span>
          <button className="primary" disabled={checkedKeys.size === 0} onClick={onConfirmPin}>
            Pin {checkedKeys.size || ''}
          </button>
          <button onClick={onCancelPin}>Cancel</button>
        </div>
      )}
      {!code || !panel ? (
        <p className="empty">Click a code in any panel to see its highlights.</p>
      ) : (
        <>
          <h2>
            {code.name} <span className="muted">· {PANEL_LABEL[panel]}</span>
            {code.rq_provenance && (
              <span className="rq-badge" title={code.rq_provenance.name}>
                RQ {code.rq_provenance.id}
              </span>
            )}
          </h2>
          {code.definition && <p className="definition">{code.definition}</p>}

          {code.exemplars.length > 0 && (
            <>
              <h3>Pinned exemplars</h3>
              {code.exemplars.map((e, i) => (
                <HighlightCard
                  key={`ex-${entryKey(e)}`}
                  entry={e}
                  pinned
                  onUnpin={panel === 'reconciled' ? () => onUnpin(code.id, i) : undefined}
                />
              ))}
            </>
          )}

          {code.highlights.length > 0 && <h3>Highlights ({code.highlights.length})</h3>}
          {code.highlights.slice(0, limit).map((e) => card(e))}

          {fromMerged.length > 0 && <h3>From merged codes ({fromMerged.length})</h3>}
          {fromMerged
            .slice(0, Math.max(0, limit - code.highlights.length))
            .map(({ entry, from }) => card(entry, `merged: ${from}`))}

          {childTotal > 0 && <h3>From child codes ({childTotal})</h3>}
          {fromChildren.slice(0, Math.max(0, limit - ownAndMerged)).map(({ e, name }) => card(e, name))}

          {shown < total && (
            <button className="more" onClick={() => setLimit(limit + PAGE)}>
              Show {Math.min(PAGE, total - shown)} more of {total - shown} remaining
            </button>
          )}

          {code.highlights.length === 0 &&
            code.exemplars.length === 0 &&
            fromMerged.length === 0 &&
            !(code.children ?? []).some((k) => k.highlights.length > 0) && (
              <p className="empty">No highlights for this code yet.</p>
            )}
        </>
      )}
    </section>
  )
}
