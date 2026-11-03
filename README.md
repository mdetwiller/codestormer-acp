# Codestormer

A codebook sensemaking prototype. See `plan/codebook-prototype-plan.md`.

## Run the app

```
cd app && npm install && npm run dev    # http://localhost:5174
```

The app reads `../data` directly; export the transcripts first or search will be empty. The reconciled codebook is kept in browser storage, so export it to keep a copy.

## Data

| Path | What it is | How it's made |
|---|---|---|
| `data/source/codebook-prep.json` | Research questions and code families exported from Notes in the Margin's `/codebook_prep` | Copied by hand; don't edit |
| `data/transcripts/conv-<id>.json` | Speaker turns per conversation (198 conversations, 41,913 turns) | `python3 scripts/export_transcripts.py` (gitignored) |
| `data/transcripts/index.json` | Conversation titles, turn counts, and which RQs cite each one | Same script |
| `data/derived/bottom_up_codebook.json` | Placeholder bottom-up codebook hand-drafted from the 7 cited conversations (`placeholder: true`) | By hand, until the LLM pass runs |
| `data/derived/top_down_codebook.json` | The prep file in the plan's `Code` shape, evidence enriched with speaker and turn index | `python3 scripts/build_top_down.py` |

`export_transcripts.py` reads `../acp_pipeline_snapshot.db` read-only (pass `--snapshot PATH` to use another copy).
Conversations 160, 162 and 201 have no transcript in the snapshot.

### Differences from the plan's data model

- The snapshot has no snippet UUID. `snippet_cortico_id` (Cortico's snippet id) stands in for `snippet_uuid`.
- `is_facilitator` is true when the turn is flagged as a facilitator prompt or the speaker is a facilitator.
- The top-down file adds a `research_questions` list, so RQs with no families (9 and 12) are still present for the pipeline.
