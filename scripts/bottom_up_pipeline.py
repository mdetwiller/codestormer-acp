"""Build the bottom-up codebook from speaker turns, adapted from the AI Sensemaking
highlights-codebook notebook. Instead of starting from Cortico highlights, Jev scores every
turn for codeable, substantive content and only turns at or above a threshold are coded.

Stages (run in order; each resumes and skips work already saved):
  score    Jev (TypeSafe's System One model) scores each turn in the chosen conversations on the
           rubric in pipeline_config.json. Consecutive turns are sent together so each is judged
           in context, one Score question per turn. Paid: TypeSafe input tokens.
  report   Free. Shows how many turns pass at each threshold, plus turns near the threshold.
  code     Open coding (the notebook's prompt): several stand-alone labels for each turn scoring
           at or above the threshold (bottom_up.threshold, or --threshold). Paid: OpenAI.
  themes   Merges near-identical labels by embedding, then asks the model for themes (parent codes)
           and sub-themes (child codes) with definitions and example labels (the notebook's codebook
           prompt plus the plan's naming rules). Paid: OpenAI.
  build    Assigns every label to its closest child code by embedding, and writes
           data/derived/bottom_up_codebook.json with each child's turns as highlights.
           Paid: one small embedding call.

Every paid stage takes --dry-run, which makes no API calls and needs no key.

Usage:
  .venv/bin/python scripts/bottom_up_pipeline.py score --dry-run
  .venv/bin/python scripts/bottom_up_pipeline.py score
  .venv/bin/python scripts/bottom_up_pipeline.py report [--threshold 2.5]
  .venv/bin/python scripts/bottom_up_pipeline.py code [--threshold 2.5] [--dry-run]
  .venv/bin/python scripts/bottom_up_pipeline.py themes [--dry-run] [--force]
  .venv/bin/python scripts/bottom_up_pipeline.py build

Changing the threshold: re-run `code` with the new value (only newly passing turns are paid for),
then `themes --force` and `build`.

Duplicate turns: some conversations contain copies of another's turns (all of conversation 1
reappears in 2, and 60 turns of 15 in 26). A turn of at least bottom_up.duplicate_min_words words
whose text also appears in another conversation is kept only in the conversation with more turns,
and its copies are skipped by every stage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from top_down_children import (  # noqa: E402
    ROOT,
    approx_tokens,
    call_model,
    clip_words,
    get_client,
    load_config,
    load_turns,
    name_warnings,
    word_count,
)

OUT_DIR = ROOT / "data" / "derived" / "generated" / "bottom_up"
SCORES = OUT_DIR / "scores.json"
OPEN_CODES = OUT_DIR / "open_codes.json"
THEMES = OUT_DIR / "themes.json"
LABEL_VECS = OUT_DIR / "label_vectors.npy"
DRY_DIR = OUT_DIR / "dry_run"
CODEBOOK = ROOT / "data" / "derived" / "bottom_up_codebook.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)


def parse_conversations(spec: str) -> set[int]:
    """'1-50' or '1-10,14,20-22' -> set of conversation ids."""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def selected_turns(cfg: dict[str, Any], spec: str | None) -> tuple[str, list[dict]]:
    spec = spec or cfg["bottom_up"]["conversations"]
    convs = parse_conversations(spec)
    turns = [t for t in load_turns() if t["conversation_id"] in convs]
    turns.sort(key=lambda t: (t["conversation_id"], t["index_in_conversation"]))
    return spec, turns


def normalized_text(text: str) -> str:
    return " ".join(text.lower().split())


def duplicate_ids(turns: list[dict], min_words: int) -> set[int]:
    """Snippet ids of turns whose text is copied from a turn in another conversation. The copy in
    the conversation with more turns (then the lower conversation id) is kept, so a conversation
    that contains a whole shorter one keeps its context. Repeats within one conversation are kept."""
    size: dict[int, int] = {}
    for t in turns:
        size[t["conversation_id"]] = size.get(t["conversation_id"], 0) + 1
    by_text: dict[str, list[dict]] = {}
    for t in turns:
        if word_count(t["text"]) >= min_words:
            by_text.setdefault(normalized_text(t["text"]), []).append(t)
    dupes: set[int] = set()
    for copies in by_text.values():
        convs = {t["conversation_id"] for t in copies}
        if len(convs) < 2:
            continue
        keep = min(convs, key=lambda c: (-size[c], c))
        dupes.update(t["snippet_id"] for t in copies if t["conversation_id"] != keep)
    return dupes


def without_duplicates(scores: dict[str, dict], dupes: set[int]) -> dict[str, dict]:
    return {sid: s for sid, s in scores.items() if int(sid) not in dupes}


# ---------- score (Jev) ----------

def chunk_turns(turns: list[dict], max_turns: int, max_words: int) -> list[dict]:
    """Consecutive turns of one conversation per chunk, capped by count and words. Each chunk
    carries the turn before it as context, which is shown but not scored."""
    chunks: list[dict] = []
    by_conv: dict[int, list[dict]] = {}
    for t in turns:
        by_conv.setdefault(t["conversation_id"], []).append(t)
    for conv_id, conv in by_conv.items():
        i = 0
        while i < len(conv):
            group, words = [conv[i]], word_count(conv[i]["text"])
            j = i + 1
            while j < len(conv) and len(group) < max_turns and words + word_count(conv[j]["text"]) <= max_words:
                group.append(conv[j])
                words += word_count(conv[j]["text"])
                j += 1
            chunks.append({
                "key": f"{conv_id}-{conv[i]['index_in_conversation']}",
                "conversation_id": conv_id,
                "context": conv[i - 1] if i > 0 else None,
                "turns": group,
            })
            i = j
    return chunks


def turn_key(t: dict) -> str:
    return f"t{t['snippet_id']}"


def speaker_label(t: dict) -> str:
    who = t.get("speaker_name") or "Unknown speaker"
    return f"{who} (facilitator)" if t["is_facilitator"] else who


def jev_request(chunk: dict, cfg: dict[str, Any]) -> dict:
    b = cfg["bottom_up"]
    ctx_words = b["context_turn_max_words"]
    state = {
        "about": b["jev_state_note"],
        "turns": (
            ([{"id": "context", "speaker": speaker_label(chunk["context"]),
               "text": clip_words(chunk["context"]["text"], ctx_words), "scored": False}]
             if chunk["context"] else [])
            + [{"id": turn_key(t), "speaker": speaker_label(t), "text": t["text"], "scored": True}
               for t in chunk["turns"]]
        ),
    }
    questions = {
        turn_key(t): {
            "type": "score",
            "instructions": b["jev_instructions"].format(turn_id=turn_key(t)),
            "criteria": b["rubric"],
        }
        for t in chunk["turns"]
    }
    return {"model": b["jev_model"], "state": state, "questions": questions}


def parse_jev_answers(chunk: dict, response: dict) -> tuple[dict[str, dict], list[int]]:
    """Scores keyed by snippet id (as str for JSON), plus ids Jev returned no answer for."""
    answers = response.get("answers", {})
    scores, missing = {}, []
    for t in chunk["turns"]:
        a = answers.get(turn_key(t))
        if not a or a.get("score") is None:
            missing.append(t["snippet_id"])
            continue
        scores[str(t["snippet_id"])] = {
            "score": round(float(a["score"]), 4),
            "confidence": round(float(a.get("confidence", 0.0)), 4),
            "probabilities": a.get("probabilities", {}),
            "chunk": chunk["key"],
        }
    return scores, missing


def read_env_var(name: str, env_file: Path) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(rf"^\s*{name}\s*=\s*(.*)\s*$", line)
            if m:
                return m.group(1).strip().strip("'\"")
    return None


def call_jev(client, url: str, key: str, body: dict, retries: int = 5) -> dict:
    delay = 2.0
    for attempt in range(retries):
        resp = client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            time.sleep(delay)
            delay *= 2
            continue
        raise RuntimeError(f"Jev returned {resp.status_code}: {resp.text[:300]}")
    raise RuntimeError("unreachable")


def stage_score(cfg: dict[str, Any], spec: str | None, dry_run: bool) -> None:
    b = cfg["bottom_up"]
    spec, turns = selected_turns(cfg, spec)
    dupes = duplicate_ids(load_turns(), b["duplicate_min_words"])
    skipped = sum(1 for t in turns if t["snippet_id"] in dupes)
    turns = [t for t in turns if t["snippet_id"] not in dupes]
    chunks = chunk_turns(turns, b["jev_chunk_turns"], b["jev_chunk_max_words"])
    saved = read_json(SCORES, {"chunks_done": [], "scores": {}, "missing": []})
    done = set(saved["chunks_done"])
    todo = [c for c in chunks
            if c["key"] not in done and any(str(t["snippet_id"]) not in saved["scores"] for t in c["turns"])]
    bodies = [jev_request(c, cfg) for c in todo]
    tokens = sum(approx_tokens(json.dumps(body)) for body in bodies)
    print(f"conversations {spec}: {len(turns):,} turns in {len(chunks)} requests "
          f"({skipped:,} duplicate turns skipped); already scored: {len(chunks) - len(todo)}; to call: {len(todo)}")
    print(f"model: {b['jev_model']}  approx input tokens: {tokens:,} (TypeSafe bills input tokens only)")
    if dry_run:
        DRY_DIR.mkdir(parents=True, exist_ok=True)
        if bodies:
            (DRY_DIR / "jev_request_example.json").write_text(json.dumps(bodies[0], ensure_ascii=False, indent=1))
            print(f"example request written to {(DRY_DIR / 'jev_request_example.json').relative_to(ROOT)}")
        return

    key = read_env_var(b["jev_key_var"], ROOT / b["jev_env_file"])
    if not key:
        sys.exit(f"{b['jev_key_var']} not set and not found in {b['jev_env_file']}")
    import httpx

    saved.setdefault("meta", {})
    saved["meta"].update({"model": b["jev_model"], "rubric": b["rubric"], "instructions": b["jev_instructions"],
                          "conversations": spec, "updated_at": now()})
    with httpx.Client(timeout=120) as client, ThreadPoolExecutor(max_workers=b["jev_concurrency"]) as pool:
        futures = {pool.submit(call_jev, client, b["jev_url"], key, body): chunk for chunk, body in zip(todo, bodies)}
        usage = 0
        for n, fut in enumerate(as_completed(futures), 1):
            chunk = futures[fut]
            try:
                response = fut.result()
            except Exception as exc:
                print(f"  {chunk['key']}: failed, will retry on the next run ({exc})")
                continue
            scores, missing = parse_jev_answers(chunk, response)
            saved["scores"].update(scores)
            saved["missing"] = sorted(set(saved["missing"]) - {int(s) for s in scores} | set(missing))
            saved["chunks_done"].append(chunk["key"])
            usage += response.get("usage", {}).get("input_tokens", 0)
            if n % 25 == 0:
                write_json(SCORES, saved)
                print(f"  {n}/{len(todo)} requests, {usage:,} input tokens so far")
    write_json(SCORES, saved)
    print(f"scored {len(saved['scores']):,} turns; no answer for {len(saved['missing'])}; "
          f"{usage:,} input tokens this run. Saved {SCORES.relative_to(ROOT)}")


# ---------- report ----------

def passing_ids(scores: dict[str, dict], threshold: float) -> list[int]:
    return sorted(int(sid) for sid, s in scores.items() if s["score"] >= threshold)


def stage_report(cfg: dict[str, Any], threshold: float) -> None:
    saved = read_json(SCORES, None)
    if not saved:
        sys.exit("run the score stage first")
    b = cfg["bottom_up"]
    all_turns = load_turns()
    dupes = duplicate_ids(all_turns, b["duplicate_min_words"])
    scores = without_duplicates(saved["scores"], dupes)
    turns_by_id = {t["snippet_id"]: t for t in all_turns}
    low_conf = b["low_confidence_below"]
    levels = len(b["rubric"])
    print(f"{len(scores):,} turns scored (conversations {saved['meta']['conversations']}; "
          f"{len(saved['scores']) - len(scores):,} duplicate turns left out); no answer for {len(saved['missing'])}")
    print("\nthreshold  turns at or above  of which participant  low confidence (<%s)" % low_conf)
    steps = sorted({step / 2 for step in range(1, (levels - 1) * 2 + 1)} | {threshold})
    for th in steps:
        ids = passing_ids(scores, th)
        part = sum(1 for i in ids if not turns_by_id[i]["is_facilitator"])
        lc = sum(1 for i in ids if scores[str(i)]["confidence"] < low_conf)
        mark = "  <- threshold" if th == threshold else ""
        print(f"  >= {th:<5}  {len(ids):>8,}           {part:>8,}           {lc:>6,}{mark}")
    near = sorted(scores.items(), key=lambda kv: abs(kv[1]["score"] - threshold))[:12]
    print(f"\nTurns closest to {threshold}:")
    for sid, s in sorted(near, key=lambda kv: -kv[1]["score"]):
        t = turns_by_id[int(sid)]
        mark = "PASS" if s["score"] >= threshold else "drop"
        print(f"  [{mark} {s['score']:.2f}, conf {s['confidence']:.2f}] conv {t['conversation_id']} "
              f"{speaker_label(t)}: {clip_words(t['text'], 40)}")


# ---------- code (open coding) ----------

OPEN_CODING_PROMPT = """You are provided speaker turns from a project aiming to '{objective}'.
The speakers are {participants}.
Each turn begins with its id in brackets. For context, the turn that came just before it is shown first; do not label the context.

Your task is to create open codes (short labels) for any significant aspect of each turn that is relevant to the project's aims.
These aspects could include concepts, sentiments, underlying meanings, and so forth.

Guidelines:
- Each label is 2-3 words and names a single concept. Never use "and", "or", "&" or "/".
- Name the pattern or meaning, not just the topic: "Distrust of officials", "Pride in heritage", "Feeling unheard", not "Local government" or "History".
- Use plain, general wording so the same idea gets the same label in different turns.
- Do not repeat ideas across labels for the same turn.
- Return every id below, each with its labels.

{turns}
"""

OPEN_CODING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["turns"],
    "properties": {
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["snippet_id", "labels"],
                "properties": {"snippet_id": {"type": "integer"}, "labels": {"type": "array", "items": {"type": "string"}}},
            },
        }
    },
}


def format_for_coding(t: dict, prev: dict | None, ctx_words: int) -> str:
    ctx = f"(context, {speaker_label(prev)}: {clip_words(prev['text'], ctx_words)})\n" if prev else ""
    return f"{ctx}[{t['snippet_id']}] {speaker_label(t)}: {t['text']}"


def coding_batches(ids: list[int], turns_by_id: dict[int, dict], max_turns: int, max_words: int) -> list[list[int]]:
    batches: list[list[int]] = []
    cur: list[int] = []
    words = 0
    for sid in ids:
        w = word_count(turns_by_id[sid]["text"])
        if cur and (len(cur) >= max_turns or words + w > max_words):
            batches.append(cur)
            cur, words = [], 0
        cur.append(sid)
        words += w
    if cur:
        batches.append(cur)
    return batches


def clean_open_codes(raw: dict, batch: list[int]) -> tuple[dict[str, list[str]], list[int], list[int], list[dict]]:
    """Keep labels only for ids that were in the batch, and only labels within the naming rules
    (2-3 words, no conjunctions). Returns (labels by id, invented ids, ids left with no labels,
    rejected labels). A turn whose labels were all rejected counts as missing, so the next run
    codes it again."""
    allowed = set(batch)
    out: dict[str, list[str]] = {}
    invented: list[int] = []
    rejected: list[dict] = []
    for item in raw.get("turns", []):
        sid = item["snippet_id"]
        if sid not in allowed:
            invented.append(sid)
            continue
        labels = []
        for label in (l.strip() for l in item["labels"] if l and l.strip()):
            problems = name_warnings(label)
            if problems:
                rejected.append({"snippet_id": sid, "label": label, "problems": problems})
            else:
                labels.append(label)
        if labels:
            out[str(sid)] = list(dict.fromkeys(out.get(str(sid), []) + labels))
    missing = [sid for sid in batch if str(sid) not in out]
    return out, invented, missing, rejected


def stage_code(cfg: dict[str, Any], threshold: float, dry_run: bool) -> None:
    b = cfg["bottom_up"]
    saved = read_json(SCORES, None)
    if not saved:
        sys.exit("run the score stage first")
    all_turns = load_turns()
    turns_by_id = {t["snippet_id"]: t for t in all_turns}
    prev_of: dict[int, dict] = {}
    by_conv: dict[int, list[dict]] = {}
    for t in all_turns:
        by_conv.setdefault(t["conversation_id"], []).append(t)
    for conv in by_conv.values():
        conv.sort(key=lambda t: t["index_in_conversation"])
        for a, c in zip(conv, conv[1:]):
            prev_of[c["snippet_id"]] = a

    dupes = duplicate_ids(all_turns, b["duplicate_min_words"])
    ids = passing_ids(without_duplicates(saved["scores"], dupes), threshold)
    coded = read_json(OPEN_CODES, {"labels": {}, "log": []})
    todo = [sid for sid in ids if str(sid) not in coded["labels"]]
    batches = coding_batches(todo, turns_by_id, b["coding_batch_turns"], b["coding_batch_max_words"])
    prompts = [
        OPEN_CODING_PROMPT.format(
            objective=b["project_objective"], participants=b["project_participants"],
            turns="\n\n".join(format_for_coding(turns_by_id[s], prev_of.get(s), b["context_turn_max_words"]) for s in batch),
        )
        for batch in batches
    ]
    print(f"threshold {threshold}: {len(ids):,} turns pass; already coded: {len(ids) - len(todo):,}; "
          f"to code: {len(todo):,} in {len(batches)} calls to {cfg['generation_model']}")
    print(f"approx input tokens: {sum(approx_tokens(p) for p in prompts):,}")
    if dry_run:
        DRY_DIR.mkdir(parents=True, exist_ok=True)
        if prompts:
            (DRY_DIR / "open_coding_prompt_example.txt").write_text(prompts[0])
            print(f"example prompt written to {(DRY_DIR / 'open_coding_prompt_example.txt').relative_to(ROOT)}")
        return

    client = get_client(cfg)
    coded["threshold"] = threshold
    coded["meta"] = {"model": cfg["generation_model"], "prompt_template": OPEN_CODING_PROMPT,
                     "project_objective": b["project_objective"], "updated_at": now()}

    def run(batch: list[int], prompt: str):
        return batch, prompt, call_model(client, cfg["generation_model"], prompt, OPEN_CODING_SCHEMA, "open_codes")

    with ThreadPoolExecutor(max_workers=b["coding_concurrency"]) as pool:
        futures = [pool.submit(run, batch, p) for batch, p in zip(batches, prompts)]
        for n, fut in enumerate(as_completed(futures), 1):
            try:
                batch, prompt, raw = fut.result()
            except Exception as exc:
                print(f"  a batch failed, will retry on the next run ({exc})")
                continue
            labels, invented, missing, rejected = clean_open_codes(raw, batch)
            coded["labels"].update(labels)
            coded["log"].append({"snippet_ids": batch, "invented_ids_removed": invented, "missing": missing,
                                 "rejected_labels": rejected,
                                 "prompt_sha1": hashlib.sha1(prompt.encode()).hexdigest(), "raw_response": raw,
                                 "generated_at": now()})
            if n % 10 == 0:
                write_json(OPEN_CODES, coded)
                print(f"  {n}/{len(batches)} calls")
    write_json(OPEN_CODES, coded)
    n_labels = sum(len(v) for v in coded["labels"].values())
    n_rejected = sum(len(entry.get("rejected_labels", [])) for entry in coded["log"])
    n_missing = len([sid for sid in ids if str(sid) not in coded["labels"]])
    print(f"{len(coded['labels']):,} turns coded, {n_labels:,} labels; {n_rejected:,} labels rejected for breaking "
          f"the 2-3 word rule; {n_missing:,} passing turns still uncoded (run again to retry them). "
          f"Saved {OPEN_CODES.relative_to(ROOT)}")


# ---------- themes ----------

def coded_at_threshold(coded: dict, scores: dict[str, dict]) -> dict[str, list[str]]:
    """Labels for turns passing the threshold of the latest code run. Turns coded at an earlier,
    lower threshold stay saved (so lowering it again costs nothing) but are left out."""
    th = coded["threshold"]
    return {sid: labels for sid, labels in coded["labels"].items() if scores.get(sid, {}).get("score", -1) >= th}

THEMES_PROMPT = """Your objective is to identify themes and sub-themes found in labels summarizing conversations with {participants}.
These conversations were part of a project aiming to '{objective}'.

Labels (each begins with its id; the number in parentheses is how many speaker turns carry that label or a near-identical one):
{labels}

Identify {specificity} themes and sub-themes, written for an audience {audience}.

Rules:
- Up to {max_parents} themes (parent codes), each with up to {max_children} sub-themes (child codes).
- Names: 2-3 words, one concept, no "and", "&" or slash. Name a pattern (what people do, feel or experience), not a topic.
- Definitions: 1-2 sentences that stand alone, specific enough that a second coder could apply the code.
- Each sub-theme lists {min_examples}-{max_examples} example_label_ids from the list above that best exemplify it. Use only ids that appear above.
- Sub-themes should not overlap each other.
"""

THEMES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["themes"],
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "definition", "sub_themes"],
                "properties": {
                    "name": {"type": "string"},
                    "definition": {"type": "string"},
                    "sub_themes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "definition", "example_label_ids"],
                            "properties": {
                                "name": {"type": "string"},
                                "definition": {"type": "string"},
                                "example_label_ids": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                },
            },
        }
    },
}


def label_occurrences(labels_by_turn: dict[str, list[str]]) -> tuple[list[str], dict[str, list[int]]]:
    """Distinct labels (case- and space-insensitive) and the turns carrying each."""
    texts: dict[str, str] = {}
    turns: dict[str, list[int]] = {}
    for sid, labels in labels_by_turn.items():
        for label in labels:
            norm = " ".join(label.lower().split())
            texts.setdefault(norm, label)
            turns.setdefault(norm, []).append(int(sid))
    keys = sorted(texts, key=lambda k: (-len(turns[k]), k))
    return [texts[k] for k in keys], {texts[k]: sorted(set(turns[k])) for k in keys}


def group_near_duplicates(vectors, threshold: float, block: int = 1024) -> list[int]:
    """Greedy grouping in the given order (most common first): each label joins the first earlier
    representative with cosine >= threshold, or becomes a representative. Returns the
    representative index for every label."""
    import numpy as np

    n = vectors.shape[0]
    rep_of = np.arange(n)
    is_rep = np.zeros(n, dtype=bool)
    for start in range(0, n, block):
        sims = vectors[start : start + block] @ vectors.T
        for r in range(sims.shape[0]):
            i = start + r
            hits = np.flatnonzero(is_rep[:i] & (sims[r, :i] >= threshold))
            if hits.size:
                rep_of[i] = hits[0]
            else:
                is_rep[i] = True
    return rep_of.tolist()


def embed_all(client, model: str, texts: list[str], batch: int = 500):
    import numpy as np

    out = []
    for start in range(0, len(texts), batch):
        resp = client.embeddings.create(model=model, input=texts[start : start + batch])
        out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
    vecs = np.array(out, dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs


def clean_themes(raw: dict, allowed_label_ids: set[str], min_examples: int, max_examples: int,
                 max_parents: int, max_children: int) -> tuple[list[dict], list[dict]]:
    themes, dropped = [], []
    for theme in raw.get("themes", [])[:max_parents]:
        kept = []
        for sub in theme["sub_themes"][:max_children]:
            ids = [i for i in dict.fromkeys(sub["example_label_ids"]) if i in allowed_label_ids][:max_examples]
            record = {"name": sub["name"].strip(), "definition": sub["definition"].strip(), "example_label_ids": ids,
                      "invented_ids_removed": [i for i in sub["example_label_ids"] if i not in allowed_label_ids],
                      "name_warnings": name_warnings(sub["name"])}
            (kept if len(ids) >= min_examples else dropped).append({**record, "theme": theme["name"]})
        if kept:
            themes.append({"name": theme["name"].strip(), "definition": theme["definition"].strip(),
                           "name_warnings": name_warnings(theme["name"]), "sub_themes": kept})
    return themes, dropped


def stage_themes(cfg: dict[str, Any], dry_run: bool, force: bool) -> None:
    b = cfg["bottom_up"]
    coded = read_json(OPEN_CODES, None)
    if not coded:
        sys.exit("run the code stage first")
    if THEMES.exists() and not force and not dry_run:
        sys.exit(f"{THEMES.relative_to(ROOT)} exists; pass --force to rebuild the themes")
    in_scope = coded_at_threshold(coded, read_json(SCORES, {"scores": {}})["scores"])
    labels, turns_of = label_occurrences(in_scope)
    print(f"threshold {coded['threshold']}: {len(in_scope):,} coded turns carry {len(labels):,} distinct labels")
    print(f"embedding the labels: {-(-len(labels) // 500)} calls, approx {sum(approx_tokens(l) for l in labels):,} tokens")
    if dry_run:
        print("then one theme call (prompt size depends on how many labels merge)")
        return

    import numpy as np

    client = get_client(cfg)
    vecs = embed_all(client, cfg["embedding_model"], labels)
    rep_of = group_near_duplicates(vecs, b["label_merge_similarity"])
    groups: dict[int, list[int]] = {}
    for i, r in enumerate(rep_of):
        groups.setdefault(r, []).append(i)
    reps = sorted(groups, key=lambda r: -len({t for i in groups[r] for t in turns_of[labels[i]]}))
    counts = {r: len({t for i in groups[r] for t in turns_of[labels[i]]}) for r in reps}
    shown = reps[: b["max_labels_in_prompt"]]
    label_id = {r: f"L{n + 1}" for n, r in enumerate(shown)}
    prompt = THEMES_PROMPT.format(
        participants=b["project_participants"], objective=b["project_objective"],
        labels="\n".join(f"[{label_id[r]}] {labels[r]} ({counts[r]})" for r in shown),
        specificity=b["specificity"], audience=b["audience"], max_parents=b["max_parents"],
        max_children=b["max_children"], min_examples=b["min_examples"], max_examples=b["max_examples"],
    )
    print(f"{len(reps):,} label groups after merging at similarity >= {b['label_merge_similarity']}; "
          f"{len(shown):,} shown to the model (~{approx_tokens(prompt):,} tokens)")
    raw = call_model(client, cfg["generation_model"], prompt, THEMES_SCHEMA, "themes")
    themes, dropped = clean_themes(raw, set(label_id.values()), b["min_examples"], b["max_examples"],
                                   b["max_parents"], b["max_children"])
    np.save(LABEL_VECS, vecs)
    write_json(THEMES, {
        "labels": labels,
        "label_turns": [turns_of[l] for l in labels],
        "rep_of": rep_of,
        "label_ids": {lid: r for r, lid in label_id.items()},
        "themes": themes,
        "dropped_sub_themes": dropped,
        "ai_metadata": {"model": cfg["generation_model"], "embedding_model": cfg["embedding_model"],
                        "prompt": prompt, "prompt_sha1": hashlib.sha1(prompt.encode()).hexdigest(),
                        "raw_response": raw, "generated_at": now()},
    })
    print(f"{len(themes)} themes, {sum(len(t['sub_themes']) for t in themes)} sub-themes "
          f"({len(dropped)} dropped for too few valid examples). Saved {THEMES.relative_to(ROOT)}")


# ---------- build ----------

def assign_labels(label_vecs, child_vecs, min_similarity: float) -> list[tuple[int, float] | None]:
    """Closest child code for every label, or None when nothing is close enough."""
    sims = label_vecs @ child_vecs.T
    best = sims.argmax(axis=1)
    return [(int(c), float(sims[i, c])) if sims[i, c] >= min_similarity else None for i, c in enumerate(best)]


def turn_entry(t: dict, source: str, insight: str) -> dict:
    n = word_count(t["text"])
    return {"connection_id": None, "conversation_id": t["conversation_id"], "insight_text": insight,
            "evidence": [{**t, "span": {"start_index": 0, "end_index": n}, "span_text": t["text"],
                          "evidence_source": source}]}


def stage_build(cfg: dict[str, Any]) -> None:
    b = cfg["bottom_up"]
    themes_file = read_json(THEMES, None)
    if not themes_file:
        sys.exit("run the themes stage first")
    import numpy as np

    saved = read_json(SCORES, {"scores": {}, "meta": {}})
    coded = read_json(OPEN_CODES, {"labels": {}})
    turns_by_id = {t["snippet_id"]: t for t in load_turns()}
    labels = themes_file["labels"]
    label_turns = themes_file["label_turns"]
    rep_of = themes_file["rep_of"]
    label_vecs = np.load(LABEL_VECS)
    children = [(ti, si, s) for ti, t in enumerate(themes_file["themes"]) for si, s in enumerate(t["sub_themes"])]

    client = get_client(cfg)
    child_vecs = embed_all(client, cfg["embedding_model"], [f"{s['name']}: {s['definition']}" for _, _, s in children])
    assigned = assign_labels(label_vecs, child_vecs, b["assign_min_similarity"])

    # The model's own example labels (and their near-duplicates) stay with the child that cited them.
    pinned: dict[int, int] = {}
    for c, (_, _, s) in enumerate(children):
        for lid in s["example_label_ids"]:
            rep = themes_file["label_ids"][lid]
            for i, r in enumerate(rep_of):
                if r == rep:
                    pinned[i] = c

    per_child: dict[int, dict[int, tuple[float, str]]] = {c: {} for c in range(len(children))}
    examples: dict[int, list[int]] = {c: [] for c in range(len(children))}
    unassigned = 0
    for i, label in enumerate(labels):
        if i in pinned:
            c, sim = pinned[i], 1.0
            examples[c].extend(label_turns[i])
        elif assigned[i] is not None:
            c, sim = assigned[i]
        else:
            unassigned += 1
            continue
        for sid in label_turns[i]:
            if sid not in per_child[c] or per_child[c][sid][0] < sim:
                per_child[c][sid] = (sim, label)

    score_of = {int(k): v["score"] for k, v in saved["scores"].items()}
    codes = []
    for ti, theme in enumerate(themes_file["themes"]):
        pid = f"bu-{slug(theme['name'])}"
        kids = []
        for c, (cti, _, sub) in enumerate(children):
            if cti != ti:
                continue
            ex = list(dict.fromkeys(examples[c]))
            rest = sorted((sid for sid in per_child[c] if sid not in ex), key=lambda sid: -per_child[c][sid][0])
            highlights = [turn_entry(turns_by_id[sid], "ai:model_example",
                                     f"Example the model cited. Open code: {per_child[c][sid][1]}") for sid in ex]
            highlights += [turn_entry(turns_by_id[sid], "ai:open_code",
                                      f"Open code: {per_child[c][sid][1]} (similarity {per_child[c][sid][0]:.2f}, "
                                      f"Jev {score_of.get(sid, 0):.1f})") for sid in rest]
            kids.append({"id": f"{pid}--{slug(sub['name'])}", "name": sub["name"], "definition": sub["definition"],
                         "source": "bottom_up", "generated": True, "name_warnings": sub["name_warnings"],
                         "highlights": highlights, "exemplars": [], "children": []})
        codes.append({"id": pid, "name": theme["name"], "definition": theme["definition"], "source": "bottom_up",
                      "generated": True, "name_warnings": theme["name_warnings"], "highlights": [], "exemplars": [],
                      "children": kids})

    ids = [c["id"] for c in codes] + [k["id"] for c in codes for k in c["children"]]
    if len(ids) != len(set(ids)):
        sys.exit("duplicate code ids after slugging; rename a theme or sub-theme in themes.json")
    th = coded["threshold"]
    n_turns = len(coded_at_threshold(coded, saved["scores"]))
    convs = saved.get("meta", {}).get("conversations", b["conversations"])
    write_json(CODEBOOK, {
        "type": "bottom_up",
        "generated_at": now(),
        "placeholder": False,
        "coverage_note": (f"Generated from speaker turns in conversations {convs} that Jev scored at or above "
                          f"{th:g} for codeable, substantive content ({n_turns:,} turns)."),
        "pipeline": {"jev_model": b["jev_model"], "rubric": b["rubric"], "threshold": th,
                     "generation_model": cfg["generation_model"], "embedding_model": cfg["embedding_model"],
                     "assign_min_similarity": b["assign_min_similarity"], "labels_unassigned": unassigned},
        "codes": codes,
    })
    total = sum(len(k["highlights"]) for c in codes for k in c["children"])
    print(f"{len(codes)} parents, {sum(len(c['children']) for c in codes)} children, {total:,} highlights; "
          f"{unassigned:,} of {len(labels):,} labels matched no child. Saved {CODEBOOK.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["score", "report", "code", "themes", "build"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--threshold", type=float,
                        help="minimum Jev score for a turn to be coded (default: bottom_up.threshold in the config)")
    parser.add_argument("--conversations", help="e.g. 1-50 (default from pipeline_config.json)")
    parser.add_argument("--force", action="store_true", help="themes: rebuild even if themes.json exists")
    args = parser.parse_args()
    cfg = load_config()
    threshold = args.threshold if args.threshold is not None else cfg["bottom_up"]["threshold"]
    if args.stage == "score":
        stage_score(cfg, args.conversations, args.dry_run)
    elif args.stage == "report":
        stage_report(cfg, threshold)
    elif args.stage == "code":
        stage_code(cfg, threshold, args.dry_run)
    elif args.stage == "themes":
        stage_themes(cfg, args.dry_run, args.force)
    else:
        stage_build(cfg)


if __name__ == "__main__":
    main()
