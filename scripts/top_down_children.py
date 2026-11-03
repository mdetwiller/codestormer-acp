"""Grow the top-down codebook from the transcripts.

Stages (run in order; each is resumable and skips work already saved):
  embed     Embed every speaker turn into data/embeddings/ (used for retrieval and, later, search).
  generate  For each code family: retrieve the closest turns to its definition plus the
            researcher's evidence, then ask the model for child codes with example turns.
            For RQs with no families, use the RQ text as the query and ask for families
            with children. Results go to data/derived/generated/top_down_children.json;
            then run scripts/build_top_down.py to merge them into the top-down codebook.

Every stage takes --dry-run, which makes no API calls and needs no key: it prints call
counts and token estimates and writes the prompts it would send to
data/derived/generated/dry_run/.

Usage:
  .venv/bin/python scripts/top_down_children.py embed --dry-run
  .venv/bin/python scripts/top_down_children.py embed
  .venv/bin/python scripts/top_down_children.py generate --dry-run
  .venv/bin/python scripts/top_down_children.py generate [--only td-family-1] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "scripts" / "pipeline_config.json"
PREP = ROOT / "data" / "source" / "codebook-prep.json"
TRANSCRIPTS = ROOT / "data" / "transcripts"
EMB_DIR = ROOT / "data" / "embeddings"
GEN_DIR = ROOT / "data" / "derived" / "generated"
RESULTS = GEN_DIR / "top_down_children.json"
DRY_DIR = GEN_DIR / "dry_run"

CHILD_NAME_MAX_WORDS = 3
CONJUNCTION = re.compile(r"\band\b|&|/", re.IGNORECASE)


# ---------- shared helpers ----------

def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def load_turns() -> list[dict]:
    index = json.loads((TRANSCRIPTS / "index.json").read_text())
    turns: list[dict] = []
    for conv in index["conversations"]:
        turns.extend(json.loads((TRANSCRIPTS / conv["file"]).read_text()))
    return turns


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def word_count(text: str) -> int:
    return len(text.split())


def clip_words(text: str, n: int) -> str:
    words = text.split()
    return text if len(words) <= n else " ".join(words[:n]) + " …"


def get_client(cfg: dict[str, Any]):
    """OpenAI client; reads OPENAI_API_KEY / OPENAI_BASE_URL from the env file named in config
    when they aren't already set. Values are never printed."""
    env_path = (ROOT / cfg["env_file"]).resolve()
    if "OPENAI_API_KEY" not in os.environ and env_path.exists():
        for line in env_path.read_text().splitlines():
            m = re.match(r"^\s*(OPENAI_API_KEY|OPENAI_BASE_URL)\s*=\s*(.*)\s*$", line)
            if m:
                os.environ.setdefault(m.group(1), m.group(2).strip().strip("'\""))
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit(f"OPENAI_API_KEY not set and not found in {env_path}")
    from openai import OpenAI

    return OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None)


# ---------- embed ----------

def embed_texts(turns: list[dict], cfg: dict[str, Any]) -> list[str]:
    limit = cfg["embedding_max_chars"]
    return [t["text"][:limit] for t in turns]


def stage_embed(cfg: dict[str, Any], dry_run: bool) -> None:
    turns = load_turns()
    texts = embed_texts(turns, cfg)
    size = cfg["embedding_batch_size"]
    batches = [list(range(i, min(i + size, len(texts)))) for i in range(0, len(texts), size)]
    chunk_dir = EMB_DIR / "chunks"
    done = {int(p.stem) for p in chunk_dir.glob("*.npy")} if chunk_dir.exists() else set()
    todo = [b for i, b in enumerate(batches) if i not in done]
    tokens = sum(approx_tokens(texts[i]) for b in todo for i in b)
    price = cfg["price_per_million_tokens_usd"].get(cfg["embedding_model"])

    print(f"turns: {len(turns)}  batches: {len(batches)}  already embedded: {len(done)}  to call: {len(todo)}")
    print(f"model: {cfg['embedding_model']}  approx tokens to send: {tokens:,}", end="")
    print(f"  approx cost: ${tokens / 1e6 * price:.2f} (assumed ${price}/M tokens)" if price else "")
    if dry_run:
        return

    import numpy as np

    client = get_client(cfg)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (EMB_DIR / "ids.json").write_text(json.dumps([t["snippet_id"] for t in turns]))
    for n, (i, batch) in enumerate((i, b) for i, b in enumerate(batches) if i not in done):
        resp = client.embeddings.create(model=cfg["embedding_model"], input=[texts[j] for j in batch])
        vecs = np.array([d.embedding for d in sorted(resp.data, key=lambda d: d.index)], dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        np.save(chunk_dir / f"{i:05d}.npy", vecs)
        if n % 25 == 0:
            print(f"  batch {i + 1}/{len(batches)}")
    (EMB_DIR / "meta.json").write_text(
        json.dumps({"model": cfg["embedding_model"], "count": len(turns), "batch_size": size,
                    "created_at": datetime.now(timezone.utc).isoformat()})
    )
    print("embeddings complete")


def load_embeddings(turn_count: int):
    import numpy as np

    ids = json.loads((EMB_DIR / "ids.json").read_text())
    chunks = sorted((EMB_DIR / "chunks").glob("*.npy"))
    matrix = np.concatenate([np.load(p) for p in chunks])
    if len(ids) != turn_count or matrix.shape[0] != len(ids):
        sys.exit(f"embeddings incomplete ({matrix.shape[0]} of {len(ids)}); run the embed stage")
    return ids, matrix


# ---------- prompts ----------

FAMILY_PROMPT = """You are extending a researcher's qualitative codebook for a study of community conversations recorded across the United States (the American Conversation Project).

Research question: {rq_name}
Code family (parent code): {name}
Family definition: {definition}

The researcher has already filed these insights under this family:
{anchors}

Below are {k} speaker turns, drawn from across all 198 conversations, that are closest in meaning to this family. Each begins with its id in brackets.

{turns}

Task: propose up to {max_children} child codes that subdivide this family, based on the patterns these turns show.

Rules:
- A child code must be supported by at least {min_examples} of the turns above. Leave out patterns that only one turn shows.
- Name: 2-3 words, one concept, no "and", "&" or slash. Name a pattern (what people do, feel or experience), not a topic.
- Definition: 1-2 sentences that stand alone, specific enough that a second coder could apply the code.
- example_snippet_ids: {min_examples}-{max_examples} ids from the bracketed list above that best exemplify the child. Use only ids that appear above.
- Child codes should not overlap each other. Turns that fit the family but no clear child can be left out.
"""

RQ_PROMPT = """You are extending a researcher's qualitative codebook for a study of community conversations recorded across the United States (the American Conversation Project).

Research question: {rq_name}
The researcher has not yet defined any code families for this question.

Below are {k} speaker turns, drawn from across all 198 conversations, that are closest in meaning to this research question. Each begins with its id in brackets.

{turns}

Task: propose up to {max_families} code families that organize how people speak to this research question, each with up to {max_children} child codes.

Rules:
- Family name: a short phrase of at most 6 words. Family definition: 1-2 sentences.
- Child name: 2-3 words, one concept, no "and", "&" or slash. Name a pattern (what people do, feel or experience), not a topic.
- Child definition: 1-2 sentences that stand alone, specific enough that a second coder could apply the code.
- Each child needs {min_examples}-{max_examples} example_snippet_ids from the bracketed list above. Use only ids that appear above.
- Only propose a family that at least two of its children's examples support. Fewer families is better than thin ones.
"""

CHILD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "definition", "example_snippet_ids"],
    "properties": {
        "name": {"type": "string"},
        "definition": {"type": "string"},
        "example_snippet_ids": {"type": "array", "items": {"type": "integer"}},
    },
}
FAMILY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["children"],
    "properties": {"children": {"type": "array", "items": CHILD_SCHEMA}},
}
RQ_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["families"],
    "properties": {
        "families": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "definition", "children"],
                "properties": {
                    "name": {"type": "string"},
                    "definition": {"type": "string"},
                    "children": {"type": "array", "items": CHILD_SCHEMA},
                },
            },
        }
    },
}


def format_turns(turns: list[dict], max_words: int) -> str:
    return "\n\n".join(
        f"[{t['snippet_id']}] (conversation {t['conversation_id']}) {clip_words(t['text'], max_words)}" for t in turns
    )


def format_anchors(family: dict) -> str:
    lines = []
    for e in family["entries"]:
        quotes = " / ".join(f'"{clip_words(ev["text"], 60)}"' for ev in e["evidence"][:2])
        lines.append(f"- {e['insight_text']}\n  Evidence: {quotes}")
    return "\n".join(lines) or "- (none)"


def family_prompt(family: dict, rq: dict, retrieved: list[dict], cfg: dict[str, Any]) -> str:
    f = cfg["families"]
    return FAMILY_PROMPT.format(
        rq_name=rq["name"], name=family["name"], definition=family["definition"],
        anchors=format_anchors(family), k=len(retrieved),
        turns=format_turns(retrieved, cfg["retrieval"]["max_words_per_turn_in_prompt"]),
        max_children=f["max_children"], min_examples=f["min_examples"], max_examples=f["max_examples"],
    )


def rq_prompt(rq: dict, retrieved: list[dict], cfg: dict[str, Any]) -> str:
    e, f = cfg["empty_rqs"], cfg["families"]
    return RQ_PROMPT.format(
        rq_name=rq["name"], k=len(retrieved),
        turns=format_turns(retrieved, cfg["retrieval"]["max_words_per_turn_in_prompt"]),
        max_families=e["max_families"], max_children=e["max_children_per_family"],
        min_examples=f["min_examples"], max_examples=f["max_examples"],
    )


def query_texts_for_family(family: dict) -> list[str]:
    texts = [f"{family['name']}: {family['definition']}"]
    texts += [ev["text"] for e in family["entries"] for ev in e["evidence"]]
    return texts


def query_texts_for_rq(rq: dict) -> list[str]:
    return [rq["name"]] + ([rq["description"]] if rq.get("description") and rq["description"] != rq["name"] else [])


# ---------- validation of model output (pure; tested) ----------

def name_warnings(name: str) -> list[str]:
    out = []
    if word_count(name) > CHILD_NAME_MAX_WORDS:
        out.append(f"more than {CHILD_NAME_MAX_WORDS} words")
    if CONJUNCTION.search(name):
        out.append("contains a conjunction")
    return out


def clean_children(raw: list[dict], allowed_ids: set[int], min_examples: int, max_examples: int) -> tuple[list[dict], list[dict]]:
    """Keep only example ids that were in the prompt; drop children left with too few examples."""
    kept, dropped = [], []
    for child in raw:
        ids = [i for i in dict.fromkeys(child.get("example_snippet_ids", [])) if i in allowed_ids][:max_examples]
        invented = [i for i in child.get("example_snippet_ids", []) if i not in allowed_ids]
        record = {
            "name": child["name"].strip(),
            "definition": child["definition"].strip(),
            "example_snippet_ids": ids,
            "invented_ids_removed": invented,
            "name_warnings": name_warnings(child["name"]),
        }
        (kept if len(ids) >= min_examples else dropped).append(record)
    return kept, dropped


def clean_rq_families(raw: list[dict], allowed_ids: set[int], cfg: dict[str, Any]) -> list[dict]:
    f = cfg["families"]
    out = []
    for fam in raw[: cfg["empty_rqs"]["max_families"]]:
        kept, dropped = clean_children(fam["children"], allowed_ids, f["min_examples"], f["max_examples"])
        if kept:
            out.append({"name": fam["name"].strip(), "definition": fam["definition"].strip(),
                        "children": kept, "dropped_children": dropped})
    return out


# ---------- generate ----------

def call_model(client, model: str, prompt: str, schema: dict, name: str) -> dict:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}},
    )
    return json.loads(resp.choices[0].message.content)


def retrieve(query_vec, matrix, ids: list[int], turns_by_id: dict[int, dict], k: int, cfg: dict[str, Any]) -> list[dict]:
    import numpy as np

    r = cfg["retrieval"]
    scores = matrix @ query_vec
    out = []
    for idx in np.argsort(-scores):
        t = turns_by_id[ids[int(idx)]]
        if r["exclude_facilitator"] and t["is_facilitator"]:
            continue
        if word_count(t["text"]) < r["min_words"]:
            continue
        out.append({**t, "similarity": float(scores[idx])})
        if len(out) >= k:
            break
    return out


def stage_generate(cfg: dict[str, Any], dry_run: bool, only: str | None, force: bool) -> None:
    prep = json.loads(PREP.read_text())
    families = [(f, rq) for rq in prep["research_questions"] for f in rq["codes"]]
    empty_rqs = [rq for rq in prep["research_questions"] if not rq["codes"]]
    jobs = [(f"td-family-{f['family_id']}", "family", f, rq) for f, rq in families]
    jobs += [(f"rq-{rq['id']}", "rq", None, rq) for rq in empty_rqs]
    if only:
        jobs = [j for j in jobs if j[0] == only]

    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {"families": {}, "empty_rqs": {}}
    have = set(results["families"]) | set(results["empty_rqs"])
    todo = [j for j in jobs if force or j[0] not in have]
    print(f"jobs: {len(jobs)}  already generated: {len(jobs) - len(todo)}  to call: {len(todo)}  model: {cfg['generation_model']}")
    print(f"plus {len(todo)} small embedding calls for the queries")

    turns = load_turns()
    turns_by_id = {t["snippet_id"]: t for t in turns}
    have_embeddings = (EMB_DIR / "meta.json").exists()

    if dry_run:
        DRY_DIR.mkdir(parents=True, exist_ok=True)
        # Without embeddings, fill prompts with a stand-in sample of long participant turns so
        # the prompt size is realistic; the real run retrieves by similarity.
        sample = [t for t in turns if not t["is_facilitator"] and word_count(t["text"]) >= cfg["retrieval"]["min_words"]][::400]
        total = 0
        for key, kind, fam, rq in todo:
            k = cfg["retrieval"]["top_k"] if kind == "family" else cfg["empty_rqs"]["top_k"]
            stand_in = (sample * 3)[:k]
            prompt = family_prompt(fam, rq, stand_in, cfg) if kind == "family" else rq_prompt(rq, stand_in, cfg)
            (DRY_DIR / f"{key}.txt").write_text(prompt)
            total += approx_tokens(prompt)
            print(f"  {key}: ~{approx_tokens(prompt):,} input tokens  ({fam['name'] if fam else rq['name']})")
        print(f"approx input tokens across calls: {total:,}")
        print(f"prompts written to {DRY_DIR.relative_to(ROOT)}/ (turns in them are a stand-in sample)")
        if not have_embeddings:
            print("note: embeddings not built yet; run the embed stage before a real generate")
        return

    if not have_embeddings:
        sys.exit("run the embed stage first")
    import numpy as np

    ids, matrix = load_embeddings(len(turns))
    client = get_client(cfg)
    GEN_DIR.mkdir(parents=True, exist_ok=True)

    for key, kind, fam, rq in todo:
        texts = query_texts_for_family(fam) if kind == "family" else query_texts_for_rq(rq)
        resp = client.embeddings.create(model=cfg["embedding_model"], input=texts)
        vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        query = vecs.mean(axis=0)
        query /= np.linalg.norm(query)

        k = cfg["retrieval"]["top_k"] if kind == "family" else cfg["empty_rqs"]["top_k"]
        retrieved = retrieve(query, matrix, ids, turns_by_id, k, cfg)
        allowed = {t["snippet_id"] for t in retrieved}
        prompt = family_prompt(fam, rq, retrieved, cfg) if kind == "family" else rq_prompt(rq, retrieved, cfg)
        raw = call_model(client, cfg["generation_model"], prompt, FAMILY_SCHEMA if kind == "family" else RQ_SCHEMA,
                         "child_codes" if kind == "family" else "families")
        ai_metadata = {
            "model": cfg["generation_model"],
            "embedding_model": cfg["embedding_model"],
            "prompt": prompt,
            "prompt_sha1": hashlib.sha1(prompt.encode()).hexdigest(),
            "query_texts": texts,
            "retrieved": [{"snippet_id": t["snippet_id"], "similarity": round(t["similarity"], 4)} for t in retrieved],
            "raw_response": raw,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if kind == "family":
            f = cfg["families"]
            kept, dropped = clean_children(raw["children"][: f["max_children"]], allowed, f["min_examples"], f["max_examples"])
            results["families"][key] = {"family_name": fam["name"], "children": kept, "dropped_children": dropped,
                                        "ai_metadata": ai_metadata}
            print(f"  {key}: {len(kept)} children kept, {len(dropped)} dropped")
        else:
            fams = clean_rq_families(raw["families"], allowed, cfg)
            results["empty_rqs"][key] = {"rq_id": rq["id"], "rq_name": rq["name"], "families": fams,
                                         "ai_metadata": ai_metadata}
            print(f"  {key}: {len(fams)} families, {sum(len(x['children']) for x in fams)} children")
        RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=1))

    print(f"saved {RESULTS.relative_to(ROOT)}; now run scripts/build_top_down.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["embed", "generate"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", help="generate one job, e.g. td-family-1 or rq-9")
    parser.add_argument("--force", action="store_true", help="regenerate jobs that already have results")
    args = parser.parse_args()
    cfg = load_config()
    if args.stage == "embed":
        stage_embed(cfg, args.dry_run)
    else:
        stage_generate(cfg, args.dry_run, args.only, args.force)


if __name__ == "__main__":
    main()
