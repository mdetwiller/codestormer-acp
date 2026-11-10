from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from bottom_up_pipeline import (  # noqa: E402
    assign_labels,
    chunk_turns,
    clean_open_codes,
    clean_themes,
    coded_at_threshold,
    duplicate_ids,
    group_near_duplicates,
    jev_request,
    label_occurrences,
    parse_conversations,
    parse_jev_answers,
)

CFG = {
    "bottom_up": {
        "jev_model": "jev-latest",
        "jev_state_note": "note",
        "jev_instructions": "Score {turn_id}",
        "rubric": ["low", "high"],
        "context_turn_max_words": 3,
    }
}


def turn(sid: int, conv: int, idx: int, text: str, fac: bool = False) -> dict:
    return {"snippet_id": sid, "conversation_id": conv, "index_in_conversation": idx, "text": text,
            "is_facilitator": fac, "speaker_name": f"S{sid}"}


def test_parse_conversations():
    assert parse_conversations("1-3,7, 9-10") == {1, 2, 3, 7, 9, 10}


def test_chunks_stay_within_a_conversation_and_carry_context():
    turns = [turn(i, 1, i, "a b c") for i in range(5)] + [turn(10 + i, 2, i, "a b c") for i in range(2)]
    chunks = chunk_turns(turns, max_turns=2, max_words=100)
    assert [[t["snippet_id"] for t in c["turns"]] for c in chunks] == [[0, 1], [2, 3], [4], [10, 11]]
    assert chunks[0]["context"] is None
    assert chunks[1]["context"]["snippet_id"] == 1
    assert chunks[3]["context"] is None


def test_chunk_word_cap_never_drops_a_long_turn():
    turns = [turn(1, 1, 0, "w " * 50), turn(2, 1, 1, "w " * 50), turn(3, 1, 2, "w")]
    chunks = chunk_turns(turns, max_turns=20, max_words=60)
    assert [[t["snippet_id"] for t in c["turns"]] for c in chunks] == [[1], [2, 3]]


def test_jev_request_scores_only_chunk_turns():
    chunk = chunk_turns([turn(1, 1, 0, "one two three four five"), turn(2, 1, 1, "hello", fac=True)], 1, 100)[1]
    body = jev_request(chunk, CFG)
    assert list(body["questions"]) == ["t2"]
    assert body["questions"]["t2"]["criteria"] == ["low", "high"]
    assert body["questions"]["t2"]["instructions"] == "Score t2"
    ctx, scored = body["state"]["turns"]
    assert ctx["scored"] is False and ctx["text"] == "one two three …"
    assert scored["speaker"] == "S2 (facilitator)"


def test_parse_jev_answers_reports_missing():
    chunk = {"key": "1-0", "turns": [turn(1, 1, 0, "x"), turn(2, 1, 1, "y")]}
    scores, missing = parse_jev_answers(chunk, {"answers": {"t1": {"score": 3.2, "confidence": 0.7, "probabilities": {"3": 0.8}}}})
    assert scores == {"1": {"score": 3.2, "confidence": 0.7, "probabilities": {"3": 0.8}, "chunk": "1-0"}}
    assert missing == [2]


def test_clean_open_codes_drops_invented_ids():
    raw = {"turns": [{"snippet_id": 5, "labels": [" Trust in neighbors ", ""]}, {"snippet_id": 99, "labels": ["x"]}]}
    labels, invented, missing, rejected = clean_open_codes(raw, [5, 6])
    assert labels == {"5": ["Trust in neighbors"]}
    assert invented == [99] and missing == [6] and rejected == []


def test_clean_open_codes_rejects_long_or_conjoined_labels():
    raw = {"turns": [
        {"snippet_id": 5, "labels": ["Feeling unheard", "Pride and belonging", "Students divided by school protests"]},
        {"snippet_id": 6, "labels": ["A label that is far too long"]},
    ]}
    labels, invented, missing, rejected = clean_open_codes(raw, [5, 6])
    assert labels == {"5": ["Feeling unheard"]}
    assert missing == [6]
    assert [r["label"] for r in rejected] == ["Pride and belonging", "Students divided by school protests",
                                             "A label that is far too long"]


def test_coded_at_threshold_ignores_turns_from_a_lower_earlier_run():
    coded = {"threshold": 3, "labels": {"1": ["a"], "2": ["b"]}}
    scores = {"1": {"score": 3.4}, "2": {"score": 2.1}}
    assert coded_at_threshold(coded, scores) == {"1": ["a"]}


def test_label_occurrences_merges_case_and_counts_turns():
    labels, turns_of = label_occurrences({"1": ["Feeling unheard"], "2": ["feeling  unheard", "Pride"], "3": ["Pride"]})
    assert labels[0] in ("Feeling unheard", "Pride") and len(labels) == 2
    assert turns_of["Feeling unheard"] == [1, 2] and turns_of["Pride"] == [2, 3]


def test_group_near_duplicates_joins_first_representative():
    v = np.array([[1, 0], [0.99, 0.141], [0, 1], [0.1, 0.995]], dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    assert group_near_duplicates(v, 0.95, block=2) == [0, 0, 2, 2]


def test_clean_themes_validates_example_ids_and_caps():
    raw = {"themes": [
        {"name": "Civic Pride", "definition": "d", "sub_themes": [
            {"name": "Local Heritage", "definition": "d", "example_label_ids": ["L1", "L2", "L404"]},
            {"name": "Thin", "definition": "d", "example_label_ids": ["L404", "L3"]},
        ]},
        {"name": "Extra", "definition": "d", "sub_themes": []},
    ]}
    themes, dropped = clean_themes(raw, {"L1", "L2", "L3"}, 2, 4, max_parents=5, max_children=8)
    assert len(themes) == 1 and themes[0]["sub_themes"][0]["example_label_ids"] == ["L1", "L2"]
    assert themes[0]["sub_themes"][0]["invented_ids_removed"] == ["L404"]
    assert dropped[0]["name"] == "Thin"


def test_assign_labels_respects_minimum():
    labels = np.array([[1, 0], [0, 1], [0.7071, 0.7071]], dtype=np.float32)
    children = np.array([[1, 0]], dtype=np.float32)
    out = assign_labels(labels, children, 0.5)
    assert out[0] == (0, 1.0) and out[1] is None and out[2][0] == 0


def test_duplicate_ids_keeps_the_copy_in_the_larger_conversation():
    long = "we never hear back from the council about anything"
    turns = [
        turn(1, 1, 0, long), turn(2, 1, 1, "Yes."),
        turn(3, 2, 0, "Hello there."), turn(4, 2, 1, long.upper() + "  "), turn(5, 2, 2, "Yes."),
        turn(6, 3, 0, long), turn(7, 3, 1, long),
    ]
    # conversations 2 and 3 tie on size, so the lower id (2) keeps it; short repeats and repeats
    # inside one conversation's kept copy are left alone
    assert duplicate_ids(turns, min_words=8) == {1, 6, 7}
