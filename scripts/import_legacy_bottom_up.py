"""Import the sensemaking-codebook-prototype's final codebook as the bottom-up codebook.

That pipeline coded Cortico AI-suggested highlights (time ranges, no snippet id) from
conversations 1-50. Each highlight is mapped to the speaker turns it overlaps, with a
word span computed from the snippet's word timings.

Usage: python3 scripts/import_legacy_bottom_up.py [--source PATH] [--snapshot PATH]
Run scripts/export_transcripts.py first.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT.parent.parent / "sensemaking-codebook-prototype" / "data" / "final_codebook_for_audit.json"
DEFAULT_SNAPSHOT = ROOT.parent / "acp_pipeline_snapshot.db"
TRANSCRIPTS = ROOT / "data" / "transcripts"
OUT = ROOT / "data" / "derived" / "bottom_up_codebook.json"

# Parents dropped at the researcher's request: two with no children and a stray "takeaways".
SLIVER_WORDS = 2
DROP_PARENTS = {"Empathy for homeless hardship", "Low turnout repeated volunteers", "takeaways"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args()

    legacy = json.loads(args.source.read_text())
    conn = sqlite3.connect(f"file:{args.snapshot}?mode=ro&immutable=1", uri=True)
    conn.execute("PRAGMA query_only = ON")

    highlight_rows = {
        r[0]: {"conversation_id": r[1], "start": r[2], "end": r[3], "source": r[4], "review_status": r[5]}
        for r in conn.execute(
            "SELECT id, conversation_id, start_offset, end_offset, source, review_status FROM highlight"
        )
    }
    snippets_by_conv: dict[int, list[tuple]] = {}
    for r in conn.execute(
        "SELECT id, conversation_id, start_offset, end_offset, words FROM snippet WHERE is_mechanical = 0"
    ):
        snippets_by_conv.setdefault(r[1], []).append(r)

    turn_cache: dict[int, dict[int, dict]] = {}

    def turns_for(conv_id: int) -> dict[int, dict]:
        if conv_id not in turn_cache:
            data = json.loads((TRANSCRIPTS / f"conv-{conv_id}.json").read_text())
            turn_cache[conv_id] = {t["snippet_id"]: t for t in data}
        return turn_cache[conv_id]

    stats = {"highlights": 0, "unmapped": 0, "evidence_turns": 0, "whole_turn_fallbacks": 0, "slivers_dropped": 0}

    def entry_for(hl_id: int) -> dict | None:
        row = highlight_rows.get(hl_id)
        if row is None:
            return None
        start, end = row["start"], row["end"]
        evidence = []
        for sid, conv_id, s_start, s_end, words_json in snippets_by_conv.get(row["conversation_id"], []):
            if not (s_start < end and s_end > start):
                continue
            words = json.loads(words_json) if words_json else []
            idx = [i for i, w in enumerate(words) if w["start"] < end and w["end"] > start]
            if idx:
                span = (idx[0], idx[-1] + 1)
                span_text = " ".join(w["word"] for w in words[span[0] : span[1]])
            else:
                stats["whole_turn_fallbacks"] += 1
                turn_text = turns_for(conv_id)[sid]["text"]
                span = (0, len(turn_text.split()))
                span_text = turn_text
            evidence.append(
                {
                    **turns_for(conv_id)[sid],
                    "span": {"start_index": span[0], "end_index": span[1]},
                    "span_text": span_text,
                    "evidence_source": f"cortico_highlight:{hl_id}",
                }
            )
        if not evidence:
            return None
        # Highlight boundaries often clip a word or two of the neighbouring turn.
        substantial = [t for t in evidence if t["span"]["end_index"] - t["span"]["start_index"] > SLIVER_WORDS]
        if substantial:
            stats["slivers_dropped"] += len(evidence) - len(substantial)
            evidence = substantial
        evidence.sort(key=lambda t: t["index_in_conversation"])
        stats["evidence_turns"] += len(evidence)
        return {
            "connection_id": None,
            "conversation_id": row["conversation_id"],
            "insight_text": "",
            "evidence": evidence,
        }

    codes = []
    for parent in legacy["parents"]:
        if parent["name"] in DROP_PARENTS:
            continue
        pid = f"bu-legacy-{slug(parent['name'])}"
        children = []
        for child in parent.get("children", []):
            highlights = []
            for h in child.get("highlights", []):
                stats["highlights"] += 1
                entry = entry_for(h["id"])
                if entry is None:
                    stats["unmapped"] += 1
                else:
                    highlights.append(entry)
            children.append(
                {
                    "id": f"{pid}--{slug(child['name'])}",
                    "name": child["name"],
                    "definition": child.get("definition", ""),
                    "source": "bottom_up",
                    "highlights": highlights,
                    "exemplars": [],
                    "children": [],
                }
            )
        codes.append(
            {
                "id": pid,
                "name": parent["name"],
                "definition": parent.get("definition", ""),
                "source": "bottom_up",
                "highlights": [],
                "exemplars": [],
                "children": children,
            }
        )

    ids = [c["id"] for c in codes] + [k["id"] for c in codes for k in c["children"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate code ids after slugging")

    convs = sorted({h["conversation_id"] for c in codes for k in c["children"] for h in k["highlights"]})
    OUT.write_text(
        json.dumps(
            {
                "type": "bottom_up",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "placeholder": False,
                "coverage_note": (
                    f"Imported from sensemaking-codebook-prototype. Covers {len(convs)} conversations "
                    f"({convs[0]}-{convs[-1]}), coded from Cortico AI-suggested highlights."
                ),
                "legacy_source": str(args.source.name),
                "dropped_parents": sorted(DROP_PARENTS),
                "codes": codes,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    print(f"{len(codes)} parents, {sum(len(c['children']) for c in codes)} children, {len(convs)} conversations")
    print(stats)


if __name__ == "__main__":
    main()
