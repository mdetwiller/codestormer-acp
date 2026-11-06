"""Similarity search over every speaker turn, for the app's search panel.

Loads the turn embeddings from data/embeddings/ (built by `top_down_children.py embed`),
embeds each query with the same model, and returns the closest turns by cosine similarity.
Each new query is one paid embedding call (a few tokens); repeated queries are cached in memory.

The app reaches it through Vite's /api proxy, so start both:
  .venv/bin/python scripts/search_server.py          # http://127.0.0.1:5175
  cd app && npm run dev

API: POST /api/search {"query": str, "limit": int} -> {"model": str, "results": [{"snippet_id", "similarity"}]}
     GET  /api/health -> {"ok": true, "turns": int, "model": str}
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from top_down_children import EMB_DIR, get_client, load_config  # noqa: E402

MAX_LIMIT = 1000
MAX_QUERY_CHARS = 2000
CACHE_SIZE = 256


def load_index() -> tuple[list[int], np.ndarray, str]:
    meta = json.loads((EMB_DIR / "meta.json").read_text())
    ids = json.loads((EMB_DIR / "ids.json").read_text())
    matrix = np.concatenate([np.load(p) for p in sorted((EMB_DIR / "chunks").glob("*.npy"))])
    if matrix.shape[0] != len(ids):
        sys.exit(f"embeddings incomplete ({matrix.shape[0]} of {len(ids)}); run the embed stage")
    return ids, matrix, meta["model"]


def rank(query_vec: np.ndarray, matrix: np.ndarray, ids: list[int], limit: int) -> list[dict]:
    """Closest turns first. Stored vectors are unit length, so the dot product is the cosine."""
    q = query_vec / (np.linalg.norm(query_vec) or 1.0)
    scores = matrix @ q
    limit = min(limit, len(ids))
    top = np.argpartition(-scores, limit - 1)[:limit]
    top = top[np.argsort(-scores[top])]
    return [{"snippet_id": ids[int(i)], "similarity": round(float(scores[i]), 4)} for i in top]


class Searcher:
    def __init__(self, ids: list[int], matrix: np.ndarray, model: str, embed: Callable[[str], np.ndarray]):
        self.ids, self.matrix, self.model, self.embed = ids, matrix, model, embed
        self.cache: OrderedDict[str, np.ndarray] = OrderedDict()

    def search(self, query: str, limit: int) -> list[dict]:
        key = " ".join(query.lower().split())
        vec = self.cache.get(key)
        if vec is None:
            vec = self.embed(query)
            self.cache[key] = vec
            if len(self.cache) > CACHE_SIZE:
                self.cache.popitem(last=False)
        else:
            self.cache.move_to_end(key)
        return rank(vec, self.matrix, self.ids, limit)


def make_handler(searcher: Searcher):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/api/health":
                self._send(200, {"ok": True, "turns": len(searcher.ids), "model": searcher.model})
            else:
                self._send(404, {"detail": "not found"})

        def do_POST(self) -> None:
            if self.path != "/api/search":
                self._send(404, {"detail": "not found"})
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                query = str(body.get("query", "")).strip()[:MAX_QUERY_CHARS]
                limit = max(1, min(int(body.get("limit", 300)), MAX_LIMIT))
            except (ValueError, TypeError):
                self._send(400, {"detail": "expected JSON {query, limit}"})
                return
            if not query:
                self._send(400, {"detail": "query is empty"})
                return
            try:
                results = searcher.search(query, limit)
            except Exception as exc:  # the embedding call failed (key, network, quota)
                self._send(502, {"detail": f"embedding call failed: {type(exc).__name__}: {exc}"})
                return
            self._send(200, {"model": searcher.model, "results": results})

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=5175)
    args = ap.parse_args()

    cfg = load_config()
    ids, matrix, model = load_index()
    client = get_client(cfg)

    def embed(text: str) -> np.ndarray:
        resp = client.embeddings.create(model=model, input=[text])
        return np.asarray(resp.data[0].embedding, dtype=np.float32)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Searcher(ids, matrix, model, embed)))
    print(f"search server: {len(ids)} turns ({model}) on http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
