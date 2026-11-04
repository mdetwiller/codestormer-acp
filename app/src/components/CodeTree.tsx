import { useState } from 'react'
import type { Placement } from '../lib/reconciled'
import type { Code, PanelId } from '../lib/types'

export const DRAG_MIME = 'application/x-codestormer-code'

export interface DragPayload {
  panel: PanelId
  id: string
}

interface Props {
  codes: Code[]
  panel: 'bottom_up' | 'top_down'
  selectedId: string | null
  onSelect: (id: string) => void
  inReconciled: Map<string, Placement>
}

export function CodeTree({ codes, panel, selectedId, onSelect, inReconciled }: Props) {
  return (
    <ul className="tree">
      {codes.map((c) => (
        <TreeNode
          key={c.id}
          code={c}
          panel={panel}
          depth={0}
          selectedId={selectedId}
          onSelect={onSelect}
          inReconciled={inReconciled}
        />
      ))}
    </ul>
  )
}

function TreeNode({
  code,
  panel,
  depth,
  selectedId,
  onSelect,
  inReconciled,
}: Omit<Props, 'codes'> & { code: Code; depth: number }) {
  const [open, setOpen] = useState(true)
  const kids = code.children ?? []
  const count = code.highlights.length + kids.reduce((n, k) => n + k.highlights.length, 0)
  const placement = inReconciled.get(code.id)
  const placed = placement !== undefined

  return (
    <li>
      <div
        className={`node depth-${depth} ${selectedId === code.id ? 'selected' : ''} ${placed ? 'placed' : ''}`}
        draggable
        onDragStart={(e) => {
          const payload: DragPayload = { panel, id: code.id }
          e.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload))
          e.dataTransfer.effectAllowed = 'copy'
        }}
        onClick={() => onSelect(code.id)}
        title={code.definition}
      >
        {kids.length > 0 ? (
          <button
            className="twisty"
            aria-label={open ? 'Collapse' : 'Expand'}
            onClick={(e) => {
              e.stopPropagation()
              setOpen(!open)
            }}
          >
            {open ? '▾' : '▸'}
          </button>
        ) : (
          <span className="twisty" />
        )}
        <span className="node-name">{code.name}</span>
        {code.generated && (
          <span
            className="ai-tag"
            title={`AI-generated from the transcripts${code.name_warnings?.length ? `; name ${code.name_warnings.join(', ')}` : ''}`}
          >
            AI
          </span>
        )}
        {code.rq_provenance && depth === 0 && (
          <span className="rq-badge" title={code.rq_provenance.name}>
            RQ {code.rq_provenance.id}
          </span>
        )}
        {placed && (
          <span
            className="placed-mark"
            title={
              placement.kind === 'merged'
                ? `Merged into "${placement.into}" in the reconciled codebook`
                : 'Already in the reconciled codebook'
            }
          >
            ✓
          </span>
        )}
        <span className="count" title="Highlights, including child codes">
          {count}
        </span>
      </div>
      {open && kids.length > 0 && (
        <ul>
          {kids.map((k) => (
            <TreeNode
              key={k.id}
              code={k}
              panel={panel}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              inReconciled={inReconciled}
            />
          ))}
        </ul>
      )}
    </li>
  )
}
