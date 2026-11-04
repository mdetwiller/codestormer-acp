import { useState } from 'react'
import { conversationsById } from '../lib/data'
import type { Entry } from '../lib/types'

interface Props {
  entry: Entry
  label?: string
  pinned?: boolean
  selectable?: boolean
  checked?: boolean
  onToggle?: () => void
  onUnpin?: () => void
}

const LONG = 60

export function HighlightCard({ entry, label, pinned, selectable, checked, onToggle, onUnpin }: Props) {
  const [expanded, setExpanded] = useState(false)
  const conv = conversationsById.get(entry.conversation_id)
  const words = entry.evidence.reduce((n, t) => n + t.span_text.split(/\s+/).length, 0)

  return (
    <article
      className={`hl ${pinned ? 'pinned' : ''} ${selectable ? 'selectable' : ''} ${checked ? 'checked' : ''}`}
      onClick={selectable ? onToggle : undefined}
    >
      <header>
        {selectable && <input type="checkbox" checked={!!checked} readOnly tabIndex={-1} />}
        {pinned && <span className="pin-star" title="Pinned exemplar">★</span>}
        <span className="conv" title={conv?.title ?? undefined}>
          Conv {entry.conversation_id}
          {conv?.title ? ` · ${conv.title}` : ''}
        </span>
        {label && <span className="hl-label">{label}</span>}
        {onUnpin && (
          <button
            className="link danger"
            onClick={(e) => {
              e.stopPropagation()
              onUnpin()
            }}
          >
            Unpin
          </button>
        )}
      </header>
      {entry.insight_text && <p className="insight">{entry.insight_text}</p>}
      <div className={`turns ${expanded || words <= LONG ? '' : 'clamped'}`}>
        {entry.evidence.map((t) => (
          <blockquote key={`${t.snippet_id}-${t.span.start_index}`}>
            <span className="speaker">
              {t.speaker_name ?? '?'}
              {t.is_facilitator ? ' (facilitator)' : ''} · turn {t.index_in_conversation}
            </span>
            {t.span_text}
          </blockquote>
        ))}
      </div>
      {words > LONG && (
        <button
          className="link"
          onClick={(e) => {
            e.stopPropagation()
            setExpanded(!expanded)
          }}
        >
          {expanded ? 'Show less' : `Show all ${words} words`}
        </button>
      )}
    </article>
  )
}
