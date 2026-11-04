from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import top_down_children as tdc  # noqa: E402

CFG = {
    "families": {"max_children": 6, "min_examples": 2, "max_examples": 4},
    "empty_rqs": {"max_families": 2, "max_children_per_family": 4, "top_k": 80},
}


def test_invented_ids_are_removed_and_thin_children_dropped():
    raw = [
        {"name": "Neighbor check-ins", "definition": "d", "example_snippet_ids": [1, 2, 999]},
        {"name": "Volunteer burnout", "definition": "d", "example_snippet_ids": [3, 998]},
    ]
    kept, dropped = tdc.clean_children(raw, {1, 2, 3}, min_examples=2, max_examples=4)
    assert [c["name"] for c in kept] == ["Neighbor check-ins"]
    assert kept[0]["example_snippet_ids"] == [1, 2]
    assert kept[0]["invented_ids_removed"] == [999]
    assert [c["name"] for c in dropped] == ["Volunteer burnout"]


def test_duplicate_ids_count_once_and_examples_are_capped():
    raw = [{"name": "Quiet giving", "definition": "d", "example_snippet_ids": [1, 1, 2, 3, 4, 5]}]
    kept, _ = tdc.clean_children(raw, {1, 2, 3, 4, 5}, min_examples=2, max_examples=4)
    assert kept[0]["example_snippet_ids"] == [1, 2, 3, 4]


def test_name_warnings_flag_long_names_and_conjunctions():
    assert tdc.name_warnings("Trust repair") == []
    assert "contains a conjunction" in tdc.name_warnings("Trust and repair")
    assert "contains a conjunction" in tdc.name_warnings("Trust/repair")
    assert any("words" in w for w in tdc.name_warnings("Slow local trust repair work"))
    assert tdc.name_warnings("Brand loyalty") == []  # "and" inside a word is fine


def test_rq_families_without_supported_children_are_dropped_and_capped():
    raw = [
        {"name": "A", "definition": "d", "children": [{"name": "x y", "definition": "d", "example_snippet_ids": [1, 2]}]},
        {"name": "B", "definition": "d", "children": [{"name": "x z", "definition": "d", "example_snippet_ids": [77]}]},
        {"name": "C", "definition": "d", "children": [{"name": "x w", "definition": "d", "example_snippet_ids": [2, 3]}]},
    ]
    fams = tdc.clean_rq_families(raw, {1, 2, 3}, CFG)
    assert [f["name"] for f in fams] == ["A"]  # B has no valid child; C is past max_families=2
