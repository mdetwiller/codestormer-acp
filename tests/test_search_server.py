from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from search_server import Searcher, rank  # noqa: E402


def unit(*v: float) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    return a / np.linalg.norm(a)


MATRIX = np.stack([unit(1, 0, 0), unit(0, 1, 0), unit(1, 1, 0), unit(0, 0, 1)])
IDS = [10, 20, 30, 40]


def test_rank_orders_by_cosine_and_respects_limit():
    out = rank(np.array([2.0, 0.1, 0.0]), MATRIX, IDS, 3)
    assert [r["snippet_id"] for r in out] == [10, 30, 20]
    assert out[0]["similarity"] > out[1]["similarity"] > out[2]["similarity"]


def test_rank_limit_larger_than_corpus():
    assert len(rank(np.array([0.0, 0.0, 1.0]), MATRIX, IDS, 99)) == 4


def test_searcher_caches_normalized_queries():
    calls: list[str] = []

    def embed(text: str) -> np.ndarray:
        calls.append(text)
        return np.array([0.0, 1.0, 0.0])

    s = Searcher(IDS, MATRIX, "fake", embed)
    first = s.search("Feeling  Unheard", 2)
    second = s.search("feeling unheard", 2)
    assert calls == ["Feeling  Unheard"]
    assert first == second and first[0]["snippet_id"] == 20
