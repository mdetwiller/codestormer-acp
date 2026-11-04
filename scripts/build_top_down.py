"""Convert data/source/codebook-prep.json into data/derived/top_down_codebook.json.

Each code family becomes a parent Code with rq_provenance; its entries become highlights,
with evidence enriched from data/transcripts (speaker, turn index, facilitator flag).
If data/derived/generated/top_down_children.json exists (scripts/top_down_children.py),
its child codes are attached under each family, and families it generated for RQs with
no families are added. Run scripts/export_transcripts.py first.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREP = ROOT / "data" / "source" / "codebook-prep.json"
TRANSCRIPTS = ROOT / "data" / "transcripts"
OUT = ROOT / "data" / "derived" / "top_down_codebook.json"
GENERATED = ROOT / "data" / "derived" / "generated" / "top_down_children.json"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def generated_code(child: dict, code_id: str, provenance: dict, cache: dict[int, dict[int, dict]], by_snippet: dict[int, int]) -> dict:
    grounding = {g["snippet_id"]: g for g in child.get("example_grounding", [])}
    picks = [(sid, "ai:model_example", grounding.get(sid)) for sid in child["example_snippet_ids"]]
    picks += [(s["snippet_id"], "ai:similarity", s) for s in child.get("similar_turns", [])]
    highlights = []
    for sid, source, score in picks:
        turn = load_turns(by_snippet[sid], cache)[sid]
        n = len(turn["text"].split())
        if source == "ai:model_example":
            note = "Example the model cited for this code"
            if score:
                note += f" (similarity {score['similarity']:.2f}, rank {score['rank_of_all_turns']:,} of all turns)"
        else:
            note = f"Found by similarity to the code's definition ({score['similarity']:.2f})"
        highlights.append(
            {
                "connection_id": None,
                "conversation_id": turn["conversation_id"],
                "insight_text": note,
                "evidence": [
                    {**turn, "span": {"start_index": 0, "end_index": n}, "span_text": turn["text"],
                     "evidence_source": source}
                ],
            }
        )
    return {
        "id": code_id,
        "name": child["name"],
        "definition": child["definition"],
        "source": "top_down",
        "rq_provenance": provenance,
        "generated": True,
        "name_warnings": child.get("name_warnings", []),
        "highlights": highlights,
        "exemplars": [],
        "children": [],
    }


def snippet_conversations() -> dict[int, int]:
    index = json.loads((TRANSCRIPTS / "index.json").read_text())
    out: dict[int, int] = {}
    for conv in index["conversations"]:
        for t in json.loads((TRANSCRIPTS / conv["file"]).read_text()):
            out[t["snippet_id"]] = conv["conversation_id"]
    return out


def load_turns(conv_id: int, cache: dict[int, dict[int, dict]]) -> dict[int, dict]:
    if conv_id not in cache:
        path = TRANSCRIPTS / f"conv-{conv_id}.json"
        cache[conv_id] = {t["snippet_id"]: t for t in json.loads(path.read_text())}
    return cache[conv_id]


def main() -> None:
    prep = json.loads(PREP.read_text())
    cache: dict[int, dict[int, dict]] = {}
    missing: list[int] = []
    codes = []
    rqs = []

    for rq in prep["research_questions"]:
        provenance = {"id": rq["id"], "name": rq["name"]}
        rqs.append(
            {
                **provenance,
                "description": rq["description"],
                "merged_from": rq["merged_from"],
                "family_ids": [f"td-family-{f['family_id']}" for f in rq["codes"]],
            }
        )
        for fam in rq["codes"]:
            highlights = []
            for entry in fam["entries"]:
                turns = load_turns(entry["conversation_id"], cache)
                evidence = []
                for ev in entry["evidence"]:
                    turn = turns.get(ev["snippet_id"])
                    if turn is None:
                        missing.append(ev["snippet_id"])
                        continue
                    evidence.append(
                        {
                            **turn,
                            "span": {"start_index": ev["start_index"], "end_index": ev["end_index"]},
                            "span_text": ev["text"],
                            "evidence_source": ev["source"],
                        }
                    )
                highlights.append(
                    {
                        "connection_id": entry["connection_id"],
                        "conversation_id": entry["conversation_id"],
                        "insight_text": entry["insight_text"],
                        "evidence": evidence,
                    }
                )
            codes.append(
                {
                    "id": f"td-family-{fam['family_id']}",
                    "name": fam["name"],
                    "definition": fam["definition"],
                    "source": "top_down",
                    "rq_provenance": provenance,
                    "highlights": highlights,
                    "exemplars": [],
                    "children": [],
                }
            )

    generated = json.loads(GENERATED.read_text()) if GENERATED.exists() else None
    n_generated = 0
    if generated:
        by_snippet = snippet_conversations()
        for code in codes:
            gen = generated["families"].get(code["id"])
            if not gen:
                continue
            code["children"] = [
                generated_code(c, f"{code['id']}--{slug(c['name'])}", code["rq_provenance"], cache, by_snippet)
                for c in gen["children"]
            ]
            n_generated += len(code["children"])
        for rq_entry in rqs:
            gen = generated["empty_rqs"].get(f"rq-{rq_entry['id']}")
            if not gen:
                continue
            provenance = {"id": rq_entry["id"], "name": rq_entry["name"]}
            for fam in gen["families"]:
                fid = f"td-gen-rq{rq_entry['id']}-{slug(fam['name'])}"
                parent = generated_code({**fam, "example_snippet_ids": []}, fid, provenance, cache, by_snippet)
                parent["children"] = [
                    generated_code(c, f"{fid}--{slug(c['name'])}", provenance, cache, by_snippet) for c in fam["children"]
                ]
                parent["name_warnings"] = []
                codes.append(parent)
                rq_entry["family_ids"].append(fid)
                n_generated += 1 + len(parent["children"])

    if missing:
        raise SystemExit(f"evidence snippet ids not found in transcripts: {missing}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "type": "top_down",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_prep_generated_at": prep["generated_at"],
                "research_questions": rqs,
                "warnings": prep.get("warnings", []),
                "codes": codes,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    n_hl = sum(len(c["highlights"]) for c in codes)
    n_ev = sum(len(h["evidence"]) for c in codes for h in c["highlights"])
    empty = [r["id"] for r in rqs if not r["family_ids"]]
    print(f"{len(codes)} families, {n_hl} highlights, {n_ev} evidence turns -> {OUT}")
    print(f"RQs with no families: {empty}")
    print(f"generated codes merged: {n_generated}" if generated else "no generated children yet")


if __name__ == "__main__":
    main()
