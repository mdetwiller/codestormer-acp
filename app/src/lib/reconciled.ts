import type { Code, Entry, ReconciledCodebook, SpeakerTurn } from './types'

export type Action =
  | { type: 'add'; code: Code; parentId: string | null }
  | { type: 'create'; name: string; definition: string; parentId: string | null }
  | { type: 'remove'; id: string }
  | { type: 'merge'; code: Code; targetId: string }
  | { type: 'unmerge'; targetId: string; index: number }
  | { type: 'rename'; id: string; name: string }
  | { type: 'define'; id: string; definition: string }
  | { type: 'move'; id: string; dir: -1 | 1 }
  | { type: 'promote'; id: string }
  | { type: 'demote'; id: string }
  | { type: 'pin'; id: string; entries: Entry[] }
  | { type: 'unpin'; id: string; index: number }
  | { type: 'clear' }
  | { type: 'replace'; codes: Code[] }

const STORAGE_KEY = 'codestormer.reconciled.v1'

export function entryKey(e: Entry): string {
  return `${e.conversation_id}:${e.evidence.map((t) => `${t.snippet_id}[${t.span.start_index}-${t.span.end_index})`).join(',')}`
}

export function turnToEntry(turn: SpeakerTurn): Entry {
  return {
    connection_id: null,
    conversation_id: turn.conversation_id,
    insight_text: '',
    evidence: [
      {
        ...turn,
        span: { start_index: 0, end_index: turn.text.split(/\s+/).filter(Boolean).length },
        span_text: turn.text,
        evidence_source: 'search',
      },
    ],
  }
}

function newId(): string {
  return `rc-${crypto.randomUUID()}`
}

/** Deep copy of a side-panel code into the reconciled panel, with fresh ids. */
function cloneForReconciled(code: Code): Code {
  return {
    ...code,
    id: newId(),
    origin_id: code.origin_id ?? code.id,
    highlights: [...code.highlights],
    exemplars: [...code.exemplars],
    children: (code.children ?? []).map(cloneForReconciled),
  }
}

function mapCode(codes: Code[], id: string, fn: (c: Code) => Code): Code[] {
  return codes.map((c) =>
    c.id === id ? fn(c) : c.children?.length ? { ...c, children: mapCode(c.children, id, fn) } : c,
  )
}

export function findCode(codes: Code[], id: string): Code | null {
  for (const c of codes) {
    if (c.id === id) return c
    const hit = findCode(c.children ?? [], id)
    if (hit) return hit
  }
  return null
}

function parentOf(codes: Code[], id: string): Code | null {
  return codes.find((c) => c.children?.some((k) => k.id === id)) ?? null
}

function strip(list: Code[], id: string): Code[] {
  return list.filter((c) => c.id !== id).map((c) => ({ ...c, children: strip(c.children ?? [], id) }))
}

/** Insert `code` as the sibling right after `afterId`, at whichever level `afterId` sits. */
function insertAfter(codes: Code[], afterId: string, code: Code): Code[] {
  const at = codes.findIndex((c) => c.id === afterId)
  if (at >= 0) return [...codes.slice(0, at + 1), code, ...codes.slice(at + 1)]
  return codes.map((c) => ({ ...c, children: insertAfter(c.children ?? [], afterId, code) }))
}

/** Every highlight a code carries, including those of codes merged into it. */
export function mergedHighlights(code: Code): { entry: Entry; from: string }[] {
  return (code.merged_from ?? []).flatMap((m) => m.highlights.map((entry) => ({ entry, from: m.name })))
}

function swap<T>(list: T[], i: number, j: number): T[] {
  if (j < 0 || j >= list.length) return list
  const next = [...list]
  ;[next[i], next[j]] = [next[j], next[i]]
  return next
}

export function reducer(codes: Code[], action: Action): Code[] {
  switch (action.type) {
    case 'add': {
      const clone = cloneForReconciled(action.code)
      if (action.parentId) {
        // The codebook is two levels deep, so a code with children can't become a child.
        // Its children are lifted to siblings under the drop target instead.
        const { children = [], ...rest } = clone
        const incoming = [{ ...rest, children: [] }, ...children]
        return mapCode(codes, action.parentId, (p) => ({
          ...p,
          children: [...(p.children ?? []), ...incoming],
        }))
      }
      return [...codes, clone]
    }
    case 'create': {
      const code: Code = {
        id: newId(),
        name: action.name,
        definition: action.definition,
        source: 'reconciled',
        highlights: [],
        exemplars: [],
        children: [],
      }
      if (action.parentId) {
        return mapCode(codes, action.parentId, (p) => ({ ...p, children: [...(p.children ?? []), code] }))
      }
      return [...codes, code]
    }
    case 'remove':
      return strip(codes, action.id)
    case 'merge': {
      // Only leaf codes merge; a code with children would lose its subtree.
      if (action.code.id === action.targetId || action.code.children?.length) return codes
      if (!findCode(codes, action.targetId)) return codes
      const inTree = findCode(codes, action.code.id)
      const src = inTree ?? cloneForReconciled(action.code)
      const rest = inTree ? strip(codes, src.id) : codes
      // Flatten: codes previously merged into the source become direct merges of the target.
      // Pinned exemplars move to the target and stay there if the code is split back out.
      const absorbed = [{ ...src, merged_from: [], exemplars: [] }, ...(src.merged_from ?? [])]
      return mapCode(rest, action.targetId, (t) => ({
        ...t,
        merged_from: [...(t.merged_from ?? []), ...absorbed],
        exemplars: [...t.exemplars, ...src.exemplars.filter((e) => !t.exemplars.some((x) => entryKey(x) === entryKey(e)))],
      }))
    }
    case 'unmerge': {
      const target = findCode(codes, action.targetId)
      const out = target?.merged_from?.[action.index]
      if (!target || !out) return codes
      const without = mapCode(codes, action.targetId, (t) => ({
        ...t,
        merged_from: (t.merged_from ?? []).filter((_, i) => i !== action.index),
      }))
      return insertAfter(without, action.targetId, out)
    }
    case 'rename':
      return mapCode(codes, action.id, (c) => ({ ...c, name: action.name }))
    case 'define':
      return mapCode(codes, action.id, (c) => ({ ...c, definition: action.definition }))
    case 'move': {
      const top = codes.findIndex((c) => c.id === action.id)
      if (top >= 0) return swap(codes, top, top + action.dir)
      const parent = parentOf(codes, action.id)
      if (!parent) return codes
      const kids = parent.children ?? []
      const i = kids.findIndex((k) => k.id === action.id)
      return mapCode(codes, parent.id, (p) => ({ ...p, children: swap(kids, i, i + action.dir) }))
    }
    case 'promote': {
      const parent = parentOf(codes, action.id)
      if (!parent) return codes
      const child = findCode(codes, action.id)!
      const without = mapCode(codes, parent.id, (p) => ({
        ...p,
        children: (p.children ?? []).filter((k) => k.id !== action.id),
      }))
      const at = without.findIndex((c) => c.id === parent.id)
      return [...without.slice(0, at + 1), { ...child, children: [] }, ...without.slice(at + 1)]
    }
    case 'demote': {
      const i = codes.findIndex((c) => c.id === action.id)
      if (i <= 0 || codes[i].children?.length) return codes
      const code = codes[i]
      const target = codes[i - 1]
      const rest = codes.filter((c) => c.id !== action.id)
      return mapCode(rest, target.id, (p) => ({ ...p, children: [...(p.children ?? []), code] }))
    }
    case 'pin':
      return mapCode(codes, action.id, (c) => {
        const have = new Set(c.exemplars.map(entryKey))
        return { ...c, exemplars: [...c.exemplars, ...action.entries.filter((e) => !have.has(entryKey(e)))] }
      })
    case 'unpin':
      return mapCode(codes, action.id, (c) => ({
        ...c,
        exemplars: c.exemplars.filter((_, i) => i !== action.index),
      }))
    case 'clear':
      return []
    case 'replace':
      return action.codes
  }
}

// Saved in IndexedDB: a reconciled codebook with merged codes runs to tens of MB, past
// localStorage's ~5 MB quota. Older builds used localStorage under the same key; it is read
// once as a fallback so that work carries over.
const DB_NAME = 'codestormer'
const DB_STORE = 'kv'

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(DB_STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function dbRequest<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb()
  try {
    return await new Promise<T>((resolve, reject) => {
      const req = run(db.transaction(DB_STORE, mode).objectStore(DB_STORE))
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
  } finally {
    db.close()
  }
}

/** The codes this browser has saved, or null if it has never saved any (not even an empty list). */
export async function loadStored(): Promise<Code[] | null> {
  try {
    const file = await dbRequest<ReconciledCodebook | undefined>('readonly', (s) => s.get(STORAGE_KEY))
    if (file) return Array.isArray(file.codes) ? file.codes : []
  } catch {
    // IndexedDB unavailable; fall through to the legacy copy.
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return null
    const parsed = JSON.parse(raw) as ReconciledCodebook
    return Array.isArray(parsed.codes) ? parsed.codes : []
  } catch {
    return null
  }
}

export async function save(codes: Code[]): Promise<void> {
  await dbRequest('readwrite', (s) => s.put(toReconciledFile(codes), STORAGE_KEY))
}

function badCode(c: unknown, path: string): string | null {
  if (!c || typeof c !== 'object') return `${path} is not an object`
  const code = c as Record<string, unknown>
  if (typeof code.id !== 'string' || typeof code.name !== 'string') return `${path} has no id or name`
  for (const key of ['highlights', 'exemplars'] as const) {
    if (code[key] !== undefined && !Array.isArray(code[key])) return `${path}.${key} is not a list`
  }
  for (const key of ['children', 'merged_from'] as const) {
    const list = code[key]
    if (list === undefined) continue
    if (!Array.isArray(list)) return `${path}.${key} is not a list`
    for (const [i, child] of list.entries()) {
      const problem = badCode(child, `${path}.${key}[${i}]`)
      if (problem) return problem
    }
  }
  return null
}

/** Parses a file written by "Export reconciled JSON"; throws a readable message for anything else. */
export function parseReconciledFile(text: string): Code[] {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    throw new Error('The file is not valid JSON.')
  }
  const codes = (data as { codes?: unknown } | null)?.codes
  if (!Array.isArray(codes)) throw new Error('The file has no "codes" list, so it is not a reconciled codebook export.')
  if (codes.some((c) => c && typeof c === 'object' && 'parent_id' in c)) {
    throw new Error('This looks like the Cortico-format export. Import the "Export reconciled JSON" file instead.')
  }
  for (const [i, c] of codes.entries()) {
    const problem = badCode(c, `codes[${i}]`)
    if (problem) throw new Error(`The file is not a reconciled codebook: ${problem}.`)
  }
  return (codes as Code[]).map((c) => ({
    ...c,
    definition: c.definition ?? '',
    highlights: c.highlights ?? [],
    exemplars: c.exemplars ?? [],
  }))
}

export function toReconciledFile(codes: Code[]): ReconciledCodebook {
  return { version: 1, last_saved: new Date().toISOString(), codes }
}

/** Where each side-panel code ended up: placed directly, or merged into a reconciled code. */
export type Placement = { kind: 'placed' } | { kind: 'merged'; into: string }

export function originIds(codes: Code[]): Map<string, Placement> {
  const out = new Map<string, Placement>()
  const walk = (list: Code[]) =>
    list.forEach((c) => {
      if (c.origin_id) out.set(c.origin_id, { kind: 'placed' })
      for (const m of c.merged_from ?? []) {
        if (m.origin_id && !out.has(m.origin_id)) out.set(m.origin_id, { kind: 'merged', into: c.name })
      }
      walk(c.children ?? [])
    })
  walk(codes)
  return out
}

const PALETTE = ['#4e79a7', '#f28e2b', '#59a14f', '#e15759', '#76b7b2', '#edc948', '#b07aa1', '#9c755f']

/** Cortico's flat codes[] with parent_id, ready to adapt to POST /v2/codebooks. */
export function toCorticoExport(codes: Code[]) {
  const flat: Record<string, unknown>[] = []
  let nextId = 1
  codes.forEach((parent, pi) => {
    const parentId = nextId++
    const color = PALETTE[pi % PALETTE.length]
    flat.push(corticoCode(parent, parentId, null, color, pi))
    ;(parent.children ?? []).forEach((child, ci) => {
      flat.push(corticoCode(child, nextId++, parentId, color, ci))
    })
  })
  return {
    title: 'Reconciled codebook',
    description: `Exported from Codestormer on ${new Date().toISOString()}`,
    codes: flat,
  }
}

function corticoCode(c: Code, id: number, parentId: number | null, color: string, order: number) {
  return {
    id,
    name: c.name,
    description: c.definition,
    parent_id: parentId,
    code_type: 'THEMATIC',
    color,
    display_order: order,
    // Not part of Cortico's schema; kept so provenance and exemplars survive the export.
    x_codestormer: {
      source: c.source,
      origin_id: c.origin_id ?? null,
      merged_from: (c.merged_from ?? []).map((m) => ({
        name: m.name,
        source: m.source,
        origin_id: m.origin_id ?? null,
        rq_provenance: m.rq_provenance ?? null,
      })),
      rq_provenance: c.rq_provenance ?? null,
      exemplar_snippet_ids: c.exemplars.flatMap((e) => e.evidence.map((t) => t.snippet_id)),
    },
  }
}
