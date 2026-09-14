"""Retrieval: BM25 over imported chunks.

No embeddings, no external service. For a 10-20 document corpus lexical
retrieval is competitive, and it has two properties that matter more here
than raw recall: it is deterministic, and it is explainable -- you can show
a vet exactly which terms matched.
"""
import math

from .. import db
from .store import tokenize

K1 = 1.5
B = 0.75


def _index():
    chunks = db.active_chunks()
    if not chunks:
        return None
    df = {}
    for c in chunks:
        for t in set(c["tokens"]):
            df[t] = df.get(t, 0) + 1
    n = len(chunks)
    avgdl = sum(len(c["tokens"]) for c in chunks) / n
    idf = {t: math.log(1 + (n - v + 0.5) / (v + 0.5)) for t, v in df.items()}
    return {"chunks": chunks, "idf": idf, "avgdl": avgdl, "n": n}


def search(query: str, top_k: int = 5):
    idx = _index()
    if not idx:
        return []
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    # Coverage is measured over distinct multi-character terms. Raw BM25 is
    # good for ranking but its absolute value moves with corpus size, so it
    # is a poor gate for "do we actually know anything about this?".
    # Coverage -- what share of the question's terms this passage contains --
    # stays on 0..1 whatever the corpus size, and is explainable to a vet.
    q_distinct = {t for t in q_tokens if len(t) > 1}

    results = []
    for c in idx["chunks"]:
        tf = {}
        for t in c["tokens"]:
            tf[t] = tf.get(t, 0) + 1
        dl = len(c["tokens"])
        score, matched = 0.0, set()
        for t in q_tokens:
            f = tf.get(t)
            if not f:
                continue
            idf = idx["idf"].get(t, 0.0)
            denom = f + K1 * (1 - B + B * dl / idx["avgdl"])
            score += idf * (f * (K1 + 1)) / denom
            if len(t) > 1:
                matched.add(t)
        if score > 0:
            coverage = len(matched) / len(q_distinct) if q_distinct else 0.0
            results.append({
                "chunk_id": c["id"], "doc_id": c["doc_id"], "title": c["title"],
                "source": c["source"], "version": c["version"],
                "published_on": c["published_on"], "locator": c["locator"],
                "text": c["text"], "score": round(score, 3),
                "coverage": round(coverage, 3),
                "matched_terms": sorted(matched)[:12],
            })
    results.sort(key=lambda r: (-r["coverage"], -r["score"]))
    return results[:top_k]
