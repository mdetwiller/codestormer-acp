# Codestormer

A codebook sensemaking prototype. See `plan/codebook-prototype-plan.md`.

## Run the app

```
cd app && npm install && npm run dev    # http://localhost:5174
```

For search by meaning, also start the similarity search server in a second terminal (from this folder, after the pipeline setup below):

```
.venv/bin/python scripts/search_server.py    # http://127.0.0.1:5175, reached by the app through Vite's /api proxy
```

It loads the turn embeddings (`data/embeddings/`, about 260 MB in memory) and embeds each new query with `text-embedding-3-small`, which is a paid call of a few tokens; repeated queries are cached. Without it, the search panel says so and offers keyword search.

The app reads `../data` directly; export the transcripts first or search will be empty. The reconciled codebook is kept in browser storage, so export it to keep a copy. "Import reconciled JSON" loads a file saved by "Export reconciled JSON" (not the Cortico-format file) and asks before replacing codes already there.

## Data

| Path | What it is | How it's made |
|---|---|---|
| `data/source/codebook-prep.json` | Research questions and code families exported from Notes in the Margin's `/codebook_prep` | Copied by hand; don't edit |
| `data/transcripts/conv-<id>.json` | Speaker turns per conversation (198 conversations, 41,913 turns) | `python3 scripts/export_transcripts.py` (gitignored) |
| `data/transcripts/index.json` | Conversation titles, turn counts, and which RQs cite each one | Same script |
| `data/derived/bottom_up_codebook.json` | Bottom-up codebook built from Jev-scored speaker turns (conversations 1-50 by default) | `scripts/bottom_up_pipeline.py` (paid; see below) |
| `data/derived/bottom_up_legacy_codebook.json` | The sensemaking-codebook-prototype's final codebook (10 parents, 75 children; conversations 1-50), highlights mapped to speaker turns by word timings. The app's left panel can switch to it | `python3 scripts/import_legacy_bottom_up.py` |
| `data/derived/generated/bottom_up/` | Jev scores, open codes and themes, with prompts and raw responses | `scripts/bottom_up_pipeline.py` |
| `data/derived/top_down_codebook.json` | The prep file in the plan's `Code` shape, evidence enriched with speaker and turn index, plus generated child codes when they exist | `python3 scripts/build_top_down.py` |
| `data/derived/generated/top_down_children.json` | AI-generated child codes and RQ 9/12 families, with prompts, retrieved turns and raw responses | `scripts/top_down_children.py generate` (paid) |
| `data/embeddings/` | `text-embedding-3-small` vectors for every turn (gitignored) | `scripts/top_down_children.py embed` (paid, ~$0.04); also used by `scripts/search_server.py` |

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

## Bottom-up pipeline (paid: TypeSafe and OpenAI)

Adapted from the AI Sensemaking `highlights-codebook.ipynb` notebook, but starting from speaker turns instead of Cortico highlights. Jev (TypeSafe's scoring model, see `plan/jev-explainer.md`) scores every turn on the rubric in `scripts/pipeline_config.json` (`bottom_up.rubric`, levels 0-4), and only turns at or above the threshold you choose are coded. The Jev key goes in `Codestormer/.env` as `TYPESAFE_API_KEY=...` (gitignored; start from `cp .env.example .env`); OpenAI calls use the same key as the top-down pipeline.

```
.venv/bin/python scripts/bottom_up_pipeline.py score --dry-run                  # free: request count, token estimate, example request
.venv/bin/python scripts/bottom_up_pipeline.py score --conversations 1-2        # pilot: check real token usage first
.venv/bin/python scripts/bottom_up_pipeline.py score                            # conversations 1-50 (585 requests)
.venv/bin/python scripts/bottom_up_pipeline.py report --threshold 2.5           # free: turns passing at each threshold
.venv/bin/python scripts/bottom_up_pipeline.py code --threshold 2.5 --dry-run   # free
.venv/bin/python scripts/bottom_up_pipeline.py code --threshold 2.5             # open coding (gpt-5.6-luna)
.venv/bin/python scripts/bottom_up_pipeline.py themes                           # label embeddings + one theme call
.venv/bin/python scripts/bottom_up_pipeline.py build                            # writes data/derived/bottom_up_codebook.json
```

Every stage resumes where it stopped. To change the threshold, re-run `code` with the new value (only newly passing turns cost anything), then `themes --force` and `build`. Every turn id and example label the model returns is checked against what it was sent. After `build`, restart the dev server: Vite doesn't notice new files in `data/`.

### Differences from the plan's data model

- The snapshot has no snippet UUID. `snippet_cortico_id` (Cortico's snippet id) stands in for `snippet_uuid`.
- `is_facilitator` is true when the turn is flagged as a facilitator prompt or the speaker is a facilitator.
- The top-down file adds a `research_questions` list, so RQs with no families (9 and 12) are still present for the pipeline.
