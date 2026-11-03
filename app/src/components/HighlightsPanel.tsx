import { entryKey } from '../lib/reconciled'
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
  const pinMode = pinTarget !== null
  const pinnedKeys = new Set((pinTarget?.exemplars ?? []).map(entryKey))

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

          {code.highlights.length > 0 && <h3>Highlights</h3>}
          {code.highlights.map((e) => card(e))}

          {(code.children ?? []).some((k) => k.highlights.length > 0) && <h3>From child codes</h3>}
          {(code.children ?? []).flatMap((k) => k.highlights.map((e) => card(e, k.name)))}

          {code.highlights.length === 0 &&
            code.exemplars.length === 0 &&
            !(code.children ?? []).some((k) => k.highlights.length > 0) && (
              <p className="empty">No highlights for this code yet.</p>
            )}
        </>
      )}
    </section>
  )
}
