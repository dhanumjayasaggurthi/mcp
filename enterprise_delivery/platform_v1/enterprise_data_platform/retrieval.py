"""Bounded reranking and diversification over already-authorized candidates."""
import json
import math
from typing import Protocol
from .durable import fingerprint


class Reranker(Protocol):
    def score(self, query: str, texts: list[str], profile: str) -> list[float]: ...


class HTTPReranker:
    def __init__(self, client):
        self.client = client

    def score(self, query, texts, profile):
        if len(texts) > 1000 or len(query) > 20000 or any(len(text) > 20000 for text in texts):
            raise ValueError('reranker input exceeds candidate or character budget')
        if not texts:
            return []
        body = {'query': query, 'documents': texts, 'model': profile}
        if len(json.dumps(body, ensure_ascii=False).encode('utf-8')) > 1_000_000:
            raise ValueError('reranker input exceeds byte budget')
        response = self.client.request('POST', '/rerank', body=body)
        rows = sorted(response['results'], key=lambda r: r['index'])
        if [r['index'] for r in rows] != list(range(len(texts))):
            raise ValueError('reranker returned an invalid batch')
        scores = [float(r['relevance_score']) for r in rows]
        if any(not math.isfinite(x) for x in scores):
            raise ValueError('reranker returned non-finite scores')
        return scores


class RetrievalPipeline:
    def __init__(self, reranker=None):
        self.reranker = reranker

    def finish(self, hits, *, query, profile, top_k):
        candidates = list(hits[:1000])
        if profile.rerank_profile:
            if not self.reranker or not query:
                raise ValueError('configured reranker is unavailable for this request')
            scores = self.reranker.score(query, [h.text or '' for h in candidates], profile.rerank_profile)
            if len(scores) != len(candidates):
                raise ValueError('reranker output does not match candidates')
            for hit, score in zip(candidates, scores):
                hit.score = score
                hit.scores['rerank'] = score
            candidates.sort(key=lambda h: (-h.score, h.chunk_id or ''))
        seen, counts, selected = set(), {}, []
        for hit in candidates:
            digest = fingerprint(hit.text) if profile.deduplicate_content else hit.chunk_id
            if digest in seen or counts.get(hit.record_id, 0) >= profile.max_chunks_per_record:
                continue
            seen.add(digest)
            counts[hit.record_id] = counts.get(hit.record_id, 0) + 1
            selected.append(hit)
        # Optional lexical MMR; semantic similarity can be supplied by a reranker.
        if profile.diversity_lambda < 1 and selected:
            pool, selected = selected, []
            tokens = {id(h): set((h.text or '').lower().split()) for h in pool}
            while pool and len(selected) < top_k:
                def utility(hit):
                    a = tokens[id(hit)]
                    overlap = max((len(a & tokens[id(other)]) / max(1, len(a | tokens[id(other)])) for other in selected), default=0)
                    return profile.diversity_lambda / (1 + pool.index(hit)) - (1-profile.diversity_lambda) * overlap
                chosen = max(pool, key=utility)
                selected.append(chosen); pool.remove(chosen)
        return selected[:top_k]
