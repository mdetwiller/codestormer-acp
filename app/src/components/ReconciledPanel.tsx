import { useState, type DragEvent } from 'react'
import type { Action } from '../lib/reconciled'
import type { Code } from '../lib/types'
import { DRAG_MIME, type DragPayload } from './CodeTree'

interface Props {
  codes: Code[]
  dispatch: (a: Action) => void
  onDropCode: (payload: DragPayload, parentId: string | null) => void
  selectedId: string | null
  onSelect: (id: string) => void
  pinTargetId: string | null
  onStartPin: (id: string) => void
}

function NewCodeForm({
  placeholder,
  onCreate,
  onCancel,
  autoFocus,
}: {
  placeholder: string
  onCreate: (name: string) => void
  onCancel?: () => void
  autoFocus?: boolean
}) {
  const [name, setName] = useState('')
  return (
    <form
      className="new-code"
      onClick={(e) => e.stopPropagation()}
      onSubmit={(e) => {
        e.preventDefault()
        const trimmed = name.trim()
        if (!trimmed) return
        onCreate(trimmed)
        setName('')
      }}
    >
      <input
        value={name}
        autoFocus={autoFocus}
        placeholder={placeholder}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') onCancel?.()
        }}
      />
      <button type="submit" disabled={!name.trim()}>
        Add
      </button>
      {onCancel && (
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      )}
    </form>
  )
}

function readPayload(e: DragEvent): DragPayload | null {
  const raw = e.dataTransfer.getData(DRAG_MIME)
  if (!raw) return null
  try {
    return JSON.parse(raw) as DragPayload
  } catch {
    return null
  }
}

export function ReconciledPanel(props: Props) {
  const { codes, onDropCode } = props
  const [over, setOver] = useState(false)

  return (
    <div
      className={`dropzone ${over ? 'over' : ''}`}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes(DRAG_MIME)) {
          e.preventDefault()
          setOver(true)
        }
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false)
      }}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const payload = readPayload(e)
        if (payload) onDropCode(payload, null)
      }}
    >
      <NewCodeForm
        placeholder="New parent code name"
        onCreate={(name) => props.dispatch({ type: 'create', name, definition: '', parentId: null })}
      />
      {codes.length === 0 ? (
        <p className="empty">
          Drag codes here from either side. Drop onto a parent code to add it as a child.
        </p>
      ) : (
        <ol className="rc-list">
          {codes.map((c, i) => (
            <RcCode key={c.id} code={c} isChild={false} first={i === 0} last={i === codes.length - 1} {...props} />
          ))}
        </ol>
      )}
    </div>
  )
}

function RcCode({
  code,
  isChild,
  first,
  last,
  dispatch,
  onDropCode,
  selectedId,
  onSelect,
  pinTargetId,
  onStartPin,
}: Props & { code: Code; isChild: boolean; first: boolean; last: boolean }) {
  const [editing, setEditing] = useState(false)
  const [overParent, setOverParent] = useState(false)
  const [addingChild, setAddingChild] = useState(false)
  const kids = code.children ?? []

  const dropHandlers = isChild
    ? {}
    : {
        onDragOver: (e: DragEvent) => {
          if (e.dataTransfer.types.includes(DRAG_MIME)) {
            e.preventDefault()
            e.stopPropagation()
            setOverParent(true)
          }
        },
        onDragLeave: () => setOverParent(false),
        onDrop: (e: DragEvent) => {
          e.preventDefault()
          e.stopPropagation()
          setOverParent(false)
          const payload = readPayload(e)
          if (payload) onDropCode(payload, code.id)
        },
      }

  return (
    <li className={isChild ? 'rc-child' : 'rc-parent'}>
      <div
        className={`rc-card src-${code.source} ${selectedId === code.id ? 'selected' : ''} ${
          pinTargetId === code.id ? 'pin-target' : ''
        } ${overParent ? 'over' : ''}`}
        onClick={() => onSelect(code.id)}
        {...dropHandlers}
      >
        <div className="rc-head">
          {editing ? (
            <input
              className="rc-name-input"
              autoFocus
              defaultValue={code.name}
              onClick={(e) => e.stopPropagation()}
              onBlur={(e) => {
                const name = e.currentTarget.value.trim()
                if (name && name !== code.name) dispatch({ type: 'rename', id: code.id, name })
                setEditing(false)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
                if (e.key === 'Escape') setEditing(false)
              }}
            />
          ) : (
            <span
              className="rc-name"
              title="Double-click to rename"
              onDoubleClick={(e) => {
                e.stopPropagation()
                setEditing(true)
              }}
            >
              {code.name}
            </span>
          )}
          <span
            className={`src-tag src-${code.source}`}
            title={
              code.rq_provenance
                ? `From the top-down codebook, under RQ ${code.rq_provenance.id}: ${code.rq_provenance.name}`
                : code.source === 'bottom_up'
                  ? 'From the bottom-up codebook'
                  : 'Created here'
            }
          >
            {code.source === 'bottom_up' ? 'bottom-up' : code.source === 'top_down' ? `RQ ${code.rq_provenance?.id ?? '?'}` : 'new'}
          </span>
          {code.exemplars.length > 0 && (
            <span className="exemplar-count" title="Pinned exemplars">
              ★ {code.exemplars.length}
            </span>
          )}
        </div>
        <textarea
          className="rc-def"
          defaultValue={code.definition}
          rows={2}
          onClick={(e) => e.stopPropagation()}
          onBlur={(e) => {
            const definition = e.currentTarget.value.trim()
            if (definition !== code.definition) dispatch({ type: 'define', id: code.id, definition })
          }}
        />
        <div className="rc-actions" onClick={(e) => e.stopPropagation()}>
          <button onClick={() => onStartPin(code.id)} title="Pick highlights to pin as exemplars">
            + Add example
          </button>
          <button disabled={first} onClick={() => dispatch({ type: 'move', id: code.id, dir: -1 })} title="Move up">
            ↑
          </button>
          <button disabled={last} onClick={() => dispatch({ type: 'move', id: code.id, dir: 1 })} title="Move down">
            ↓
          </button>
          {!isChild && (
            <button onClick={() => setAddingChild(true)} title="Create a new child code under this one">
              + Child
            </button>
          )}
          {isChild ? (
            <button onClick={() => dispatch({ type: 'promote', id: code.id })} title="Make this a parent code">
              Promote
            </button>
          ) : (
            <button
              disabled={first || kids.length > 0}
              onClick={() => dispatch({ type: 'demote', id: code.id })}
              title={
                kids.length > 0
                  ? 'Only a code without children can become a child'
                  : 'Make this a child of the code above'
              }
            >
              Demote
            </button>
          )}
          <button className="danger" onClick={() => dispatch({ type: 'remove', id: code.id })} title="Remove">
            ✕
          </button>
        </div>
      </div>
      {kids.length > 0 && (
        <ol className="rc-children">
          {kids.map((k, i) => (
            <RcCode
              key={k.id}
              code={k}
              isChild
              first={i === 0}
              last={i === kids.length - 1}
              codes={kids}
              dispatch={dispatch}
              onDropCode={onDropCode}
              selectedId={selectedId}
              onSelect={onSelect}
              pinTargetId={pinTargetId}
              onStartPin={onStartPin}
            />
          ))}
        </ol>
      )}
      {addingChild && (
        <div className="rc-children">
          <NewCodeForm
            autoFocus
            placeholder={`New child of ${code.name}`}
            onCreate={(name) => {
              dispatch({ type: 'create', name, definition: '', parentId: code.id })
              setAddingChild(false)
            }}
            onCancel={() => setAddingChild(false)}
          />
        </div>
      )}
    </li>
  )
}
