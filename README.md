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
| `data/derived/bottom_up_codebook.json` | The sensemaking-codebook-prototype's final codebook (10 parents, 75 children; conversations 1-50), highlights mapped to speaker turns by word timings | `python3 scripts/import_legacy_bottom_up.py` |
| `data/derived/top_down_codebook.json` | The prep file in the plan's `Code` shape, evidence enriched with speaker and turn index, plus generated child codes when they exist | `python3 scripts/build_top_down.py` |
| `data/derived/generated/top_down_children.json` | AI-generated child codes and RQ 9/12 families, with prompts, retrieved turns and raw responses | `scripts/top_down_children.py generate` (paid) |
| `data/embeddings/` | `text-embedding-3-small` vectors for every turn (gitignored) | `scripts/top_down_children.py embed` (paid, ~$0.04) |

`export_transcripts.py` reads `../acp_pipeline_snapshot.db` read-only (pass `--snapshot PATH` to use another copy).
Conversations 160, 162 and 201 have no transcript in the snapshot.

## Top-down pipeline (paid OpenAI calls)

```
python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt
.venv/bin/python scripts/top_down_children.py embed --dry-run      # free: counts and cost estimate
.venv/bin/python scripts/top_down_children.py embed                # 420 calls
.venv/bin/python scripts/top_down_children.py generate --dry-run   # free: writes prompts to data/derived/generated/dry_run/
.venv/bin/python scripts/top_down_children.py generate             # 10 calls to gpt-5.5, plus 10 query embeddings
python3 scripts/build_top_down.py                                   # merge the results into the top-down codebook
```

The key comes from `OPENAI_API_KEY` / `OPENAI_BASE_URL`, or from the env file named in `scripts/pipeline_config.json` (the sensemaking pipeline's `backend/.env`). Both stages resume and skip work already saved; `generate --only td-family-1 --force` redoes one family. Every example id the model returns is checked against the turns it was shown. Tests: `.venv/bin/python -m pytest -q tests`.

### Differences from the plan's data model

- The snapshot has no snippet UUID. `snippet_cortico_id` (Cortico's snippet id) stands in for `snippet_uuid`.
- `is_facilitator` is true when the turn is flagged as a facilitator prompt or the speaker is a facilitator.
- The top-down file adds a `research_questions` list, so RQs with no families (9 and 12) are still present for the pipeline.
