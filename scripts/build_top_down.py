"""Convert data/source/codebook-prep.json into data/derived/top_down_codebook.json.

Each code family becomes a parent Code with rq_provenance; its entries become highlights,
with evidence enriched from data/transcripts (speaker, turn index, facilitator flag).
Child codes are left empty here; the top-down pipeline fills them later.
Run scripts/export_transcripts.py first.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREP = ROOT / "data" / "source" / "codebook-prep.json"
TRANSCRIPTS = ROOT / "data" / "transcripts"
OUT = ROOT / "data" / "derived" / "top_down_codebook.json"


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


if __name__ == "__main__":
    main()
