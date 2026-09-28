"""Optional semantic search: local multilingual embeddings of the stored corpus.

Install with `pip install "prawnik-mcp[semantic]"` and build the index with `prawnik-mcp embed`. Search then
merges the lexical (FTS5) ranking with the embedding ranking by reciprocal rank fusion. Everything runs on
the local machine; the model is downloaded once from Hugging Face by fastembed (ONNX, no PyTorch).

The index covers only documents stored locally: live source APIs take words, not vectors. Vectors are
compared by brute force (NumPy), which is fine for hundreds of thousands of chunks and not beyond.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Iterable
from typing import Any

from prawnik_mcp.store import Store

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 384-d, ~220 MB
CHUNK_CHARS = 600  # the default model reads about 128 tokens
MAX_CHUNKS_PER_RECORD = 4
BATCH = 32
_model_cache: dict[str, Any] = {}


def model_name() -> str:
    return os.environ.get("PRAWNIK_MCP_EMBED_MODEL", DEFAULT_MODEL)


def available() -> bool:
    try:
        import fastembed  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


def enabled(store: Store) -> bool:
    """Semantic ranking is used when the extra is installed, the index is not empty and it is not switched off."""
    if os.environ.get("PRAWNIK_MCP_SEMANTIC", "") in ("0", "false", "no") or not available():
        return False
    try:
        return indexed_count(store) > 0
    except Exception:  # noqa: BLE001
        return False


def _model():
    name = model_name()
    if name not in _model_cache:
        from fastembed import TextEmbedding

        _model_cache[name] = TextEmbedding(model_name=name)
    return _model_cache[name]


def embed(texts: list[str]):
    """L2-normalised float32 matrix, one row per text."""
    import numpy as np

    vecs = np.array(list(_model().embed(texts, batch_size=BATCH)), dtype="float32")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-9)


def chunks(title: str, body: str) -> list[str]:
    """Up to MAX_CHUNKS_PER_RECORD pieces of ~CHUNK_CHARS from the start of the record, cut at sentence ends."""
    text = re.sub(r"\s+", " ", body).strip()
    out: list[str] = []
    while text and len(out) < MAX_CHUNKS_PER_RECORD:
        if len(text) <= CHUNK_CHARS:
            piece, text = text, ""
        else:
            cut = max(text.rfind(". ", 0, CHUNK_CHARS), text.rfind("; ", 0, CHUNK_CHARS))
            cut = cut + 1 if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
            piece, text = text[:cut], text[cut:].lstrip()
        out.append(f"{title}. {piece}" if title and not out else piece)
    return out


def _digest(model: str, pieces: list[str]) -> str:
    return hashlib.sha256((model + "\x00" + "\x00".join(pieces)).encode()).hexdigest()[:16]


def index_store(store: Store, progress: Callable[[str], None] | None = None, *, limit: int | None = None,
                source_ids: list[str] | None = None) -> dict[str, int]:
    """Embed records that are new or changed. Returns counts."""
    import numpy as np

    model = model_name()
    have = {r: h for r, h in store.db.execute("SELECT ref, digest FROM embeddings WHERE model=? AND chunk=0", (model,))}
    q = "SELECT ref, kind, document_id, title, body FROM fts"
    args: list = []
    if source_ids:
        q += " WHERE source_id IN ({})".format(",".join("?" * len(source_ids)))
        args = list(source_ids)
    todo: list[tuple[str, str, str, list[str], str]] = []
    total = skipped = 0
    for ref, kind, doc_id, title, body in store.db.execute(q, args).fetchall():
        pieces = chunks(title, body)
        if not pieces:
            continue
        total += 1
        digest = _digest(model, pieces)
        if have.get(ref) == digest:
            skipped += 1
            continue
        todo.append((ref, kind, doc_id, pieces, digest))
        if limit and len(todo) >= limit:
            break
    done = 0
    for i in range(0, len(todo), 64):
        batch = todo[i:i + 64]
        flat = [p for _, _, _, pieces, _ in batch for p in pieces]
        vecs = embed(flat)
        pos = 0
        with store.batch():
            for ref, kind, doc_id, pieces, digest in batch:
                store.db.execute("DELETE FROM embeddings WHERE ref=? AND model=?", (ref, model))
                for n, piece in enumerate(pieces):
                    store.db.execute(
                        "INSERT INTO embeddings(ref, chunk, model, kind, document_id, digest, text, vec) VALUES (?,?,?,?,?,?,?,?)",
                        (ref, n, model, kind, doc_id, digest, piece[:700], np.asarray(vecs[pos], dtype="float32").tobytes()))
                    pos += 1
        done += len(batch)
        if progress:
            progress(f"embedded {done}/{len(todo)} records")
    _matrix_cache.clear()
    return {"records": total, "embedded": done, "unchanged": skipped}


_matrix_cache: dict[tuple, Any] = {}


def _load(store: Store, model: str):
    import numpy as np

    n = store.db.execute("SELECT count(*), coalesce(max(rowid),0) FROM embeddings WHERE model=?", (model,)).fetchone()
    key = (str(store.data_dir), model, n[0], n[1])
    if key not in _matrix_cache:
        rows = store.db.execute("SELECT ref, kind, document_id, text, vec FROM embeddings WHERE model=? ORDER BY rowid",
                                (model,)).fetchall()
        _matrix_cache.clear()
        if rows:
            mat = np.frombuffer(b"".join(r[4] for r in rows), dtype="float32").reshape(len(rows), -1)
        else:
            mat = np.zeros((0, 1), dtype="float32")
        _matrix_cache[key] = (rows, mat)
    return _matrix_cache[key]


def indexed_count(store: Store) -> int:
    return store.db.execute("SELECT count(*) FROM embeddings WHERE model=? AND chunk=0", (model_name(),)).fetchone()[0]


def search(store: Store, query: str, *, kinds: Iterable[str] | None = None, limit: int = 20
           ) -> list[tuple[str, str, str, float, str]]:
    """Best records by cosine similarity: (ref, kind, document_id, score, matching chunk text)."""
    import numpy as np

    rows, mat = _load(store, model_name())
    if not rows:
        return []
    qv = embed([query])[0]
    sims = mat @ qv
    kindset = set(kinds) if kinds else None
    best: dict[str, tuple[float, int]] = {}
    for idx in np.argsort(-sims)[: max(limit * 6, 60)]:
        ref, kind = rows[idx][0], rows[idx][1]
        if kindset and kind not in kindset:
            continue
        s = float(sims[idx])
        if ref not in best or s > best[ref][0]:
            best[ref] = (s, int(idx))
    out = []
    for ref, (s, idx) in sorted(best.items(), key=lambda kv: -kv[1][0])[:limit]:
        r = rows[idx]
        out.append((ref, r[1], r[2], s, r[3]))
    return out


def fuse(lexical: list[str], semantic: list[str], *, k: int = 60) -> list[str]:
    """Reciprocal rank fusion of two ranked lists of refs."""
    score: dict[str, float] = {}
    for ranking in (lexical, semantic):
        for rank, ref in enumerate(ranking):
            score[ref] = score.get(ref, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score, key=lambda r: -score[r])
