"""Export speaker turns from the Cortico pipeline snapshot into per-conversation JSON.

Writes data/transcripts/conv-<id>.json (SpeakerTurn[]) and data/transcripts/index.json.
The snapshot is opened read-only and immutable; this script never writes to it.

Usage: python3 scripts/export_transcripts.py [--snapshot PATH]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT = ROOT.parent / "acp_pipeline_snapshot.db"
OUT_DIR = ROOT / "data" / "transcripts"
PREP = ROOT / "data" / "source" / "codebook-prep.json"


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


def cited_rqs() -> dict[int, set[int]]:
    """conversation_id -> RQ ids whose prep entries cite it."""
    cites: dict[int, set[int]] = defaultdict(set)
    if not PREP.exists():
        return cites
    prep = json.loads(PREP.read_text())
    for rq in prep["research_questions"]:
        for fam in rq["codes"]:
            for entry in fam["entries"]:
                cites[entry["conversation_id"]].add(rq["id"])
    return cites


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args()

    conn = connect(args.snapshot)
    rows = conn.execute(
        """
        SELECT s.id, s.cortico_id, s.conversation_id, s.participant_id, s.order_index,
               s.start_offset, s.end_offset, s.text, s.is_facilitator_prompt,
               p.name AS speaker_name, p.is_facilitator AS participant_is_facilitator
        FROM snippet s
        LEFT JOIN participant p ON p.id = s.participant_id
        WHERE s.is_mechanical = 0
        ORDER BY s.conversation_id, s.order_index
        """
    ).fetchall()
    conversations = {
        r["id"]: r
        for r in conn.execute(
            "SELECT id, cortico_id, project_id, title, duration_seconds FROM conversation"
        )
    }

    turns: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        turns[r["conversation_id"]].append(
            {
                "snippet_id": r["id"],
                # The snapshot has no snippet UUID; Cortico's snippet id stands in for it.
                "snippet_cortico_id": r["cortico_id"],
                "conversation_id": r["conversation_id"],
                "speaker_id": r["participant_id"],
                "speaker_name": r["speaker_name"],
                "is_facilitator": bool(r["is_facilitator_prompt"] or r["participant_is_facilitator"]),
                "text": r["text"],
                "index_in_conversation": r["order_index"],
                "audio_start_offset": r["start_offset"],
                "audio_end_offset": r["end_offset"],
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("conv-*.json"):
        old.unlink()

    cites = cited_rqs()
    index = []
    for conv_id in sorted(turns):
        path = OUT_DIR / f"conv-{conv_id}.json"
        path.write_text(json.dumps(turns[conv_id], ensure_ascii=False, indent=1))
        c = conversations.get(conv_id)
        index.append(
            {
                "conversation_id": conv_id,
                "cortico_id": c["cortico_id"] if c else None,
                "project_id": c["project_id"] if c else None,
                "title": c["title"] if c else None,
                "duration_seconds": c["duration_seconds"] if c else None,
                "turn_count": len(turns[conv_id]),
                "facilitator_turn_count": sum(t["is_facilitator"] for t in turns[conv_id]),
                "cited_by_rq_ids": sorted(cites.get(conv_id, ())),
                "file": path.name,
            }
        )

    without = sorted(set(conversations) - set(turns))
    (OUT_DIR / "index.json").write_text(
        json.dumps(
            {
                "source": str(args.snapshot.name),
                "conversation_count": len(index),
                "turn_count": sum(i["turn_count"] for i in index),
                "conversations_without_transcript": without,
                "conversations": index,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    print(f"{len(index)} conversations, {len(rows)} turns -> {OUT_DIR}")
    print(f"no transcript: {without}")


if __name__ == "__main__":
    main()
