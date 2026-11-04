# Codebook Sensemaking Prototype — Plan

*AI Sensemaking Sprint · Updated 2026-11-03 with data decisions*

---

## Overview

A browser-based sensemaking tool that holds two independently-generated draft codebooks side by side — one built bottom-up from the conversation corpus, one built top-down from the researcher's existing analytic framework — and lets the researcher construct a reconciled codebook by dragging codes from either panel into a center workspace. The tool is grounded throughout in real conversation highlights and includes a RAG search portal for on-demand evidence retrieval.

The core methodological tension the interface makes visible: **what the data wants to say** vs. **what the research questions came in looking for.**

---

## Data Layout

This folder is its own repo (`github.com/mdetwiller/codestormer-acp`), sitting inside the Notes in the Margin checkout so it can read the snapshot one level up.

```
Codestormer/
  plan/codebook-prototype-plan.md
  data/
    source/codebook-prep.json        # exported from Notes in the Margin /codebook_prep; don't edit
    transcripts/conv-<id>.json       # SpeakerTurn[] for 198 conversations, 41,913 turns (gitignored, regenerable)
    transcripts/index.json           # title, project, turn counts, cited_by_rq_ids per conversation
    derived/top_down_codebook.json   # built by scripts/build_top_down.py
    derived/bottom_up_codebook.json  # placeholder now; LLM pipeline output later
  scripts/export_transcripts.py      # reads ../acp_pipeline_snapshot.db read-only
  scripts/build_top_down.py
  app/                               # Vite + React interface
```

The corpus is **198 conversations** (the snapshot has 201; three have no transcript), not the 250 assumed in the first draft.

---

## Two Pipelines

### 1. Bottom-Up Codebook (topic-driven)

**Goal:** Produce a two-level codebook (parent code → child codes) derived purely from the corpus, blind to the researcher's existing framework.

**Pipeline steps:**

1. **Ingest** — Load all 198 conversation transcripts from `data/transcripts/` (see Data Layout). Each transcript is a sequence of speaker turns (one speaker's continuous utterance before the next speaker begins).
2. **Open coding** — For each speaker turn in the corpus (all participants, not filtered), prompt an LLM to assign a short open code (2–3 words, no conjunctions, pattern not topic).
3. **Cluster** — Collect all open codes across the corpus. Prompt an LLM to cluster them into a two-level hierarchy: parent codes and child codes. Each parent and child code gets:
   - Name: 2–3 words, single concept, no "X and Y" structure
   - Definition: 1–2 sentences
   - Example highlights: 2–4 representative speaker turns
4. **Output** — JSON file: `bottom_up_codebook.json`

**Test scope:** Start with 50 conversations. Full run after interface validation.

**Note:** Transcripts come from the local Cortico pipeline snapshot (`acp_pipeline_snapshot.db`), not the live API, so no fetch step is needed. Field names follow the snapshot mapping under Data Model; cross-check against the V2 API docs only for the final export to `POST /v2/codebooks`.

---

### 2. Top-Down Codebook (research-question-driven)

**Goal:** Extend the researcher's existing analytic framework into a full two-level codebook populated with evidence from the full corpus.

**Input:** `data/source/codebook-prep.json` (converted to `data/derived/top_down_codebook.json` by `scripts/build_top_down.py`) — contains research questions, code families (with definitions and existing entries/highlights from ~5–10 conversations each).

**Pipeline steps:**

1. **Embed** — Embed every speaker turn once (`text-embedding-3-small`); the query for each code family is the mean of its definition's and its existing evidence snippets' embeddings.
2. **Retrieve** — For each code family, retrieve semantically similar speaker turns from the full 198-conversation corpus.
3. **Generate child codes** — Prompt an LLM to cluster retrieved turns into child codes under each family. Same naming constraints as bottom-up (2–3 words, no conjunctions).
4. **Handle empty research questions** — For RQs with no code families yet (currently RQ 9 on change/navigation and RQ 12 on technology), use the RQ description itself as the retrieval query, then generate both code families and their child codes in a single pass.
5. **Output** — JSON file: `top_down_codebook.json`

**Hierarchy:**
```
Research Question (interface framing, not a codebook node)
  └── Code Family (parent code)
        └── Child Codes
```

Research questions organize the top-down panel in the interface but are not structural nodes in the exported codebook.

---

## Interface Design

**Tech stack:** React (Vite), no backend for the prototype — all data from local JSON files.

**Layout:** Three-panel workspace

```
┌─────────────────┬──────────────┬─────────────────────────────┐
│  Bottom-Up      │   CENTER     │  Top-Down                   │
│  Codebook       │  Reconciled  │  Codebook                   │
│  (topic-driven) │  Codebook    │  (RQ-driven)                │
│                 │              │                             │
│  Parent code    │  Drop zone   │  Code Family [RQ badge]     │
│    Child code   │              │    Child code               │
│    Child code   │  Codes       │    Child code               │
│  Parent code    │  dragged     │  Code Family [RQ badge]     │
│    …            │  here from   │    …                        │
│                 │  either side │                             │
│                 │              │                             │
├─────────────────┴──────────────┴─────────────────────────────┤
│  HIGHLIGHTS PANEL (always visible, docked to bottom)         │
│  Shows highlights for the currently selected code            │
│                           ·  ·  ·                            │
│  [RAG SEARCH] — semantic search across all 198 conversations  │
└──────────────────────────────────────────────────────────────┘
```

### Left panel — Bottom-Up Codebook

- Collapsible tree: parent codes at top level, child codes nested
- Click any code to surface its example highlights in the highlights panel
- Drag a parent code (with its children) to the center drop zone

### Right panel — Top-Down Codebook

- Collapsible tree: code family → child codes
- Each code family carries a provenance badge showing which research question it originated from — visible inline but not a navigation filter
- Research questions do not appear as structural nodes or tabs; they are metadata on the codes
- Click to surface highlights; drag to center
- When a code is dragged to the center, its RQ provenance travels with it (stored in the Code object, surfaced on hover in the reconciled panel)

### Center panel — Reconciled Codebook

- Drop zone accepts codes from either side
- When a parent code is dropped, its child codes come with it
- Codes in the center are editable: rename, reorder, promote/demote a child code to parent
- Each code has an "Add example" affordance — opens the highlights panel in selection mode so the researcher can pin a highlight as a canonical exemplar for that code
- Pinned exemplars are visually distinct and will eventually become tagging instructions
- Export button: saves the reconciled codebook as JSON

### Highlights panel (bottom, always visible)

- Shows highlights attached to the currently selected code
- Each highlight shows: conversation ID, speaker turn text, insight memo (if from top-down)
- In selection mode (triggered by "Add example"), highlights are checkable; confirming adds them to the selected code's exemplars in the center

### RAG search portal (right side of highlights panel)

- Semantic search box
- Scoped to all 198 conversations, indexed by speaker turn
- Results show: matching turn text, conversation ID, similarity score
- Any result can be dragged into the highlights panel for a code, or used to find more evidence for a code under consideration

---

## Data Model

*Reconciled with Cortico V2 API. The pipeline and interface use the Cortico shapes for all API calls; the interface's internal representation flattens and extends them for rendering.*

---

### Cortico V2 API shapes (canonical)

**Snippet** — a speaker turn in the transcript (one person's uninterrupted speech block):
```typescript
interface Snippet {
  id: number
  uuid: string
  content: string
  conversation_id: number
  speaker_id: number
  speaker_name: string
  is_facilitator: boolean   // true for facilitators; open coding covers all turns
  index_in_conversation: number
  words: Word[]             // word-level timing/index metadata
  audio_start_offset: number
  audio_end_offset: number
}
```
Fetched via: `GET /v2/conversations/{conversation_id}/transcript` → `SnippetList`

**Highlight** — a saved excerpt from a conversation, with human memo:
```typescript
interface Highlight {
  id: number
  content: string           // the highlighted text
  conversation_id: number
  title: string
  description: string       // human insight memo
  user_id: number
  tags: string[]
  type: string
}
```
Word-level span stored as `SnippetWordIndices { snippet_uuid: string, start_index: number, end_index: number }`.

**Code** — a single node in a codebook; flat array, hierarchy via `parent_id`:
```typescript
interface CorticoCode {
  id: number
  name: string              // 2–3 words, no conjunctions
  description: string       // definition
  codebook_id: number
  parent_id: number | null  // null = top-level parent; non-null = child code
  code_type: "STRUCTURAL" | "THEMATIC" | "INTERNAL" | "OTHER"
  color: string
  display_order: number
}
```
Generated codes use `code_type: "THEMATIC"`.

**Codebook** — named container for a set of codes:
```typescript
interface CorticoCodebook {
  id: number
  title: string
  description: string
  organization_id: number
  project_id: number
  catalog_id: number
  codes: CorticoCode[]      // flat array; hierarchy expressed via parent_id
}
```

**Coding** — applies a code to a highlight:
```typescript
interface Coding {
  id: number
  code_id: number
  highlight_share_id: number
  is_applied: boolean
  is_suggested: boolean
  ai_metadata: object       // provenance for LLM-generated codings
}
```

---

### Interface-internal shapes

The interface works from local JSON files (Phase 1) and does not call the Cortico API directly. These shapes are the normalized forms stored in `bottom_up_codebook.json`, `top_down_codebook.json`, and `reconciled_codebook.json`.

**SpeakerTurn** — normalized from the snapshot's `snippet` table (one file per conversation in `data/transcripts/`):
```typescript
interface SpeakerTurn {
  snippet_id: number          // snapshot snippet.id (the id the prep JSON cites)
  snippet_cortico_id: number  // snapshot snippet.cortico_id; stands in for Snippet.uuid, which the snapshot lacks
  conversation_id: number
  speaker_id: number          // snapshot snippet.participant_id
  speaker_name: string        // participant.name; often just "A", "B"
  is_facilitator: boolean     // snippet.is_facilitator_prompt OR participant.is_facilitator
  text: string                // Cortico Snippet.content
  index_in_conversation: number  // snippet.order_index
  audio_start_offset: number
  audio_end_offset: number
}
```
Mechanical snippets (`is_mechanical`) are excluded; there are none in the current snapshot. Word-level timing (`words`) is not exported.

**Entry** — normalized from researcher's `codebook-prep.json` entries:
```typescript
interface Entry {
  connection_id: number     // from prep JSON
  conversation_id: number
  insight_text: string      // human memo (Highlight.description)
  evidence: EvidenceTurn[]  // the Snippets cited, with the highlighted word span
}

interface EvidenceTurn extends SpeakerTurn {
  span: { start_index: number, end_index: number }  // half-open word range [start, end)
  span_text: string         // the highlighted words only
  evidence_source: "user" | "ai"  // who attached the evidence in Notes in the Margin
}
```

**Code** — shared shape for both generated codebooks and the reconciled codebook:
```typescript
interface Code {
  id: string                // generated UUID for interface; maps to CorticoCode.id when exported
  name: string              // 2–3 words, no conjunctions
  definition: string
  source: "bottom_up" | "top_down" | "reconciled"
  rq_provenance?: {         // top-down codes only: the RQ this family was generated under
    id: number
    name: string
  }
  highlights: Entry[]       // example evidence
  exemplars: Entry[]        // researcher-pinned; become tagging instructions
  children?: Code[]         // nested for rendering; exported flat with parent_id
}
```

**Codebook** — local JSON root:
```typescript
interface Codebook {
  type: "bottom_up" | "top_down"
  generated_at: string
  codes: Code[]             // top-level parent codes; children nested under each
  // top-down only:
  research_questions?: {    // every RQ, including ones with no families (RQ 9, RQ 12)
    id: number, name: string, description: string, merged_from: number[], family_ids: string[]
  }[]
  warnings?: string[]       // carried over from the prep JSON
  // bottom-up placeholder only:
  placeholder?: boolean     // true until the LLM stub pass replaces it
}
```
Top-down family ids are `td-family-<family_id>` so they stay stable across rebuilds.

**ReconciledCodebook**:
```typescript
interface ReconciledCodebook {
  version: number
  last_saved: string
  codes: Code[]             // researcher's edited selection, source field preserved
}
```

**Export shape** — when the researcher exports the reconciled codebook, it serializes to Cortico's flat `codes[]` format with `parent_id` references, ready to `POST /v2/codebooks`.

---

## Build Phases

### Phase 1 — Interface prototype (current focus)
- [x] Organize data: export transcripts from the snapshot, move the prep JSON under `data/source/`
- [x] Transform `codebook-prep.json` into `top_down_codebook.json` shape (`scripts/build_top_down.py`)
- [x] Scaffold React app (Vite) in `app/` (dev server on port 5174, so it doesn't clash with Notes in the Margin on 5173)
- [x] Stub data: a placeholder `bottom_up_codebook.json` hand-drafted from real participant turns in the 7 cited conversations (5 parents, 15 children, 47 highlights; marked `placeholder: true`). Replace with a quick LLM pass once that paid run is approved
- [x] Build three-panel layout
- [x] Left panel: render bottom-up tree, click-to-highlight
- [x] Right panel: render top-down tree with RQ provenance badges (not tabs or filters), click-to-highlight; RQs with no families listed underneath
- [x] Center panel: drop zone, drag-and-drop from either side; drop onto a parent to nest. Codes stay two levels deep: a parent dropped onto a parent comes in with its children lifted to siblings
- [x] Collapse parent codes in the center (▾/▸ on each parent, plus Collapse all / Expand all); a collapsed parent shows one line with its child-code count and still accepts drops
- [x] Merge child codes: drop a child code (from the center by its ⠿ grip, or from either side panel) onto a center child code. The target keeps its name and definition; merged codes are kept whole under `merged_from`, their highlights show under the target, their exemplars move to it, and "split" restores one as a separate child. Codes with children can't be merged. Side-panel codes merged into a reconciled code get the same ✓ as placed codes, with "Merged into …" on hover
- [x] Center editing: create new parent codes and child codes, rename (double-click), edit definition, reorder (↑ ↓), promote, demote (only codes without children), remove. Saved to browser storage
- [x] Highlights panel: surfaces highlights for selected code, plus its child codes' highlights
- [x] "Add example" flow: pick highlights from any code or from search results → pin to the code in the center
- [x] RAG search: keyword stub over all 198 conversations (top 50, facilitator turns hidden by default)
- [x] Export reconciled codebook as JSON, plus a Cortico flat `codes[]` export with `parent_id` (provenance kept under `x_codestormer`)
- [ ] Drag search results into the highlights panel (pinning uses checkboxes for now)
- [x] Replace the bottom-up placeholder with the sensemaking-codebook-prototype's final codebook (`scripts/import_legacy_bottom_up.py`): 10 parents and 75 children after dropping "Empathy for homeless hardship", "Low turnout repeated volunteers" and "takeaways". It covers conversations 1-50 only and was coded from Cortico AI-suggested highlights, mapped to speaker turns by word timings
- [x] Top-down child codes (`scripts/top_down_children.py`, OpenAI, run 2026-11-03 with the Notes in the Margin key): embedded all 41,913 turns (`text-embedding-3-small`); retrieved the 60 closest participant turns per family (definition plus the researcher's evidence as the query); one `gpt-5.6-luna` call per family produced 48 child codes; RQ 9 and RQ 12 got 3 families each (21 children) from the RQ text. An evidence check (`expand`) attached the 15 most similar turns to each child, so each child has 17-19 highlights

### Phase 2 — Real pipelines
- [ ] Full bottom-up pipeline: open coding → clustering on 50 conversations
- [ ] Full top-down pipeline: embedding retrieval + child code generation for all code families
- [ ] Real RAG: embed all 198 conversations by speaker turn, serve similarity search

### Phase 3 — Tagging integration
- [ ] Exemplar highlights → formatted tagging instructions
- [ ] LLM tagging pass (pairwise judgment model TBD)
- [ ] Human/LLM judgment averaging

---

## Open Questions

1. **Cortico V2 API data model** — needed only for the final export (`POST /v2/codebooks`) and to confirm whether `snippet_cortico_id` can replace `snippet_uuid`. Not blocking Phase 1 or 2, since transcripts come from the snapshot.
2. ~~**Conversation transcript paths**~~ — resolved: transcripts come from `acp_pipeline_snapshot.db` (198 of 201 conversations have transcripts; 160, 162 and 201 do not), exported by `scripts/export_transcripts.py`.
3. **Open coding granularity** — currently: one open code per speaker turn. Should very long turns (multi-paragraph) be split, or coded as one?
4. **Bottom-up hierarchy depth** — currently: two levels (parent → child). Should the parent level be named/editable by the researcher before they see child codes, or is the LLM-generated name the starting point?
5. **RAG scope** — currently scoped to 198 conversations. Should researcher-pinned exemplars from the existing JSON also be searchable?
