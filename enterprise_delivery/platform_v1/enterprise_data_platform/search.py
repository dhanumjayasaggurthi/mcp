"""OpenSearch BM25 and filtered ANN adapters with bounded, checked bulk writes."""
from __future__ import annotations
import json
import math
import re
from dataclasses import dataclass

import httpx
from .backends import EmbeddingProvider, KeywordBackend, VectorBackend
from .cache import BoundedCache, CacheClass, CacheKey
from .chunks import retrieval_version
from .context import current_context, current_actor
from .durable import fingerprint
from .models import RetrievalHit
from .query_validation import referenced_filter_fields
from .resilience import BackendUnavailable, CircuitBreaker


def index_name(dataset, version, kind):
    if kind not in {'keyword', 'vector'}:
        raise ValueError('invalid index kind')
    return 'edp-' + fingerprint([dataset, version])[:40] + '-' + kind


def search_filter(expr):
    """Compile TRUE/FALSE predicates separately to preserve SQL NULL semantics."""
    referenced_filter_fields(expr)
    def both(node):
        all_ = lambda xs: {'bool': {'filter': xs}}
        any_ = lambda xs: {'bool': {'should': xs, 'minimum_should_match': 1}}
        negate = lambda x: {'bool': {'must_not': [x]}}
        if not node:
            return {'match_all': {}}, {'match_none': {}}
        if 'not' in node:
            yes, no = both(node['not']); return no, yes
        if 'and' in node or 'or' in node:
            key = 'and' if 'and' in node else 'or'
            pairs = [both(x) for x in node[key]]
            yes, no = [x[0] for x in pairs], [x[1] for x in pairs]
            return (all_(yes), any_(no)) if key == 'and' else (any_(yes), all_(no))
        field, op, value = 'metadata.'+node['field'], node['op'], node.get('value')
        exists = {'exists': {'field': field}}
        if op == 'exists' or (op in {'eq','neq'} and value is None):
            present = (True if value is None else value) if op == 'exists' else op == 'neq'
            return (exists, negate(exists)) if present else (negate(exists), exists)
        if op in {'eq','neq'}:
            yes = {'term': {field: value}}
        elif op == 'in':
            yes = {'terms': {field: [x for x in value if x is not None]}}
        elif op in {'gt','gte','lt','lte'}:
            yes = {'range': {field: {op: value}}}
        elif op == 'between':
            yes = {'range': {field: {'gte': value[0], 'lte': value[1]}}}
        else:
            raise ValueError('filter operator is not supported by search metadata index')
        no = {'match_none': {}} if op == 'in' and None in value else all_([exists, negate(yes)])
        return (no,yes) if op == 'neq' else (yes,no)
    return both(expr)[0]


@dataclass(frozen=True)
class SearchSettings:
    shards: int = 3
    replicas: int = 2
    refresh_interval: str = '5s'
    bulk_size: int = 200
    dimensions: int = 1536
    ef_construction: int = 128
    m: int = 16


class OpenSearchTransport:
    def __init__(self, endpoint, token=None, *, client=None, timeout=10):
        from urllib.parse import urlsplit
        parsed = urlsplit(endpoint)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('search endpoint requires credential-free HTTPS')
        self.client = client or httpx.Client(base_url=endpoint.rstrip('/'), timeout=timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={'Authorization': 'Bearer ' + token} if token else {}, follow_redirects=False, trust_env=False)
        self.timeout, self.breaker = timeout, CircuitBreaker()

    def request(self, method, path, *, body=None, content=None, idempotent=True):
        context = current_context.get()
        def perform():
            seconds = min(self.timeout, context.remaining() if context else self.timeout)
            with self.client.stream(method, path, json=body, content=content, timeout=seconds,
                headers={'Content-Type': 'application/x-ndjson'} if content is not None else None) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    raise BackendUnavailable('search service unavailable')
                if response.status_code >= 300:
                    raise ValueError('search request rejected')
                raw = bytearray()
                for part in response.iter_bytes():
                    if context:
                        context.remaining()
                    raw.extend(part)
                    if len(raw) > 16_000_000:
                        raise ValueError('search response exceeds byte budget')
            value = json.loads(raw)
            if value.get('timed_out') or value.get('_shards', {}).get('failed', 0):
                raise BackendUnavailable('search response is incomplete')
            return value
        try:
            return self.breaker.call(perform, retryable=(httpx.TransportError, BackendUnavailable), attempts=3 if idempotent else 1)
        except httpx.TransportError as exc:
            raise BackendUnavailable('upstream transport unavailable') from exc

    def close(self):
        self.client.close()


class OpenSearchBackend(KeywordBackend, VectorBackend):
    def __init__(self, transport, kind):
        self.transport, self.kind = transport, kind

    def search(self, *, product, filter_expr, top_k, query=None, vector=None):
        if not 1 <= top_k <= product.max_top_k:
            raise ValueError('top-k exceeds dataset budget')
        actor = current_actor.get() or {}
        acl_filters = []
        for name, identities in [('subjects', [actor.get('subject', '')]), ('groups', actor.get('groups', []))]:
            acl_filters.append({'bool': {'should': [{'bool': {'must_not': [{'exists': {'field': 'acl.' + name}}]}},
                {'terms': {'acl.' + name: identities}}], 'minimum_should_match': 1}})
        constraint = {'bool': {'filter': [search_filter(filter_expr), *acl_filters]}}
        if self.kind == 'keyword':
            query_dsl = {'bool': {'must': [{'match': {'text': {'query': query, 'operator': 'or'}}}], 'filter': [constraint]}}
        else:
            profile = product.retrieval.vector
            if len(vector) != profile.dimensions or any(not math.isfinite(float(v)) for v in vector):
                raise ValueError('invalid query embedding')
            query_dsl = {'knn': {'vector': {'vector': list(vector), 'k': top_k, 'filter': constraint}}}
        value = self.transport.request('POST', '/' + index_name(product.id, retrieval_version(product), self.kind) + '/_search',
            body={'size': top_k, 'track_total_hits': False, 'timeout': '10s',
                '_source': ['record_id', 'chunk_id', 'metadata', 'source_version', 'content_hash'], 'query': query_dsl})
        hits = value.get('hits', {}).get('hits', [])
        if len(hits) > top_k:
            raise ValueError('search backend exceeded candidate budget')
        return [RetrievalHit(record_id=h['_source']['record_id'], chunk_id=h['_source']['chunk_id'],
            score=h['_score'] or 0, metadata=h['_source'].get('metadata', {}),
            scores={self.kind: h['_score'] or 0}) for h in hits]


class OpenSearchSink:
    def __init__(self, transport, kind, settings=None):
        self.transport, self.kind, self.settings = transport, kind, settings or SearchSettings()

    def provision(self, product, version):
        settings = self.settings
        properties = {'acl': {'properties': {'subjects': {'type': 'keyword'}, 'groups': {'type': 'keyword'}}}, 'record_id': {'type': 'keyword'}, 'chunk_id': {'type': 'keyword'},
            'source_version': {'type': 'keyword'}, 'content_hash': {'type': 'keyword'},
            'metadata': {'type': 'object', 'dynamic': 'strict', 'properties': {}}}
        for f in product.fields:
            if f.filterable or f.vector_metadata:
                kind = {'int': 'long', 'integer': 'long', 'float': 'double', 'boolean': 'boolean', 'date': 'date', 'datetime': 'date'}.get(f.data_type.lower(), 'keyword')
                properties['metadata']['properties'][f.name] = {'type': kind}
        index_settings = {'number_of_shards': settings.shards, 'number_of_replicas': settings.replicas,
            'refresh_interval': settings.refresh_interval}
        if self.kind == 'keyword':
            properties['text'] = {'type': 'text', 'analyzer': 'standard'}
        else:
            index_settings['index.knn'] = True
            properties['vector'] = {'type': 'knn_vector', 'dimension': product.retrieval.vector.dimensions,
                'method': {'name': 'hnsw', 'engine': 'lucene', 'space_type': 'cosinesimil',
                    'parameters': {'ef_construction': settings.ef_construction, 'm': settings.m}}}
        return self.transport.request('PUT', '/' + index_name(product.id, version, self.kind),
            body={'settings': index_settings, 'mappings': {'dynamic': 'strict', 'properties': properties}}, idempotent=False)

    def write_record(self, *, product, version, canonical, vectors, sequence, old_chunk_ids):
        index = index_name(product.id, version, self.kind)
        new_ids = {c.chunk_id for c in canonical}
        actions = []
        for chunk_id in sorted(set(old_chunk_ids) - new_ids):
            actions.append(({'delete': {'_index': index, '_id': chunk_id, 'version': sequence + 1, 'version_type': 'external_gte'}}, None))
        for i, chunk in enumerate(canonical):
            document = {'acl': chunk.acl, 'record_id': chunk.record_id, 'chunk_id': chunk.chunk_id, 'metadata': chunk.metadata,
                'source_version': chunk.source_version, 'content_hash': chunk.content_hash}
            if self.kind == 'keyword':
                document['text'] = chunk.text
            else:
                document['vector'] = vectors[i]
            actions.append(({'index': {'_index': index, '_id': chunk.chunk_id, 'version': sequence + 1, 'version_type': 'external_gte'}}, document))
        for start in range(0, len(actions), self.settings.bulk_size):
            batch = actions[start:start+self.settings.bulk_size]
            lines = [json.dumps(v, separators=(',', ':')) for pair in batch for v in pair if v is not None]
            content = '\n'.join(lines) + '\n'
            if len(content.encode()) > 8_000_000:
                raise ValueError('index bulk request exceeds byte budget')
            result = self.transport.request('POST', '/_bulk?refresh=wait_for', content=content.encode())
            items = result.get('items', [])
            if len(items) != len(batch):
                raise BackendUnavailable('incomplete bulk acknowledgement')
            for item in items:
                op, value = next(iter(item.items()))
                if value['status'] >= 300 and not (op == 'delete' and value['status'] == 404):
                    raise BackendUnavailable('partial bulk write failure')


class HTTPEmbeddingProvider(EmbeddingProvider):
    """Batch protocol: POST /embeddings {model,input,dimensions}; indexed data[]."""
    def __init__(self, client, models, *, batch_size=64):
        self.client, self.models, self.batch_size = client, models, min(batch_size, 128)
        self.cache = BoundedCache(max_entries=2048, ttl_seconds=3600, max_bytes=16_000_000)

    def embed(self, text, *, profile_id, dimensions):
        return self.embed_batch([text], profile_id=profile_id, dimensions=dimensions)[0]

    def embed_batch(self, texts, *, profile_id, dimensions, security_scope=None):
        if len(texts) > 1000:
            raise ValueError('embedding batch exceeds record budget')
        security_scope = security_scope or fingerprint(current_actor.get() or {'system': 'indexing'})
        if profile_id not in self.models:
            raise ValueError('embedding profile is not registered')
        model = self.models[profile_id]
        result = [None] * len(texts)
        pending = []
        for i, text_value in enumerate(texts):
            if len(text_value) > 20000:
                raise ValueError('embedding text exceeds configured input budget')
            key = CacheKey(CacheClass.EMBEDDING, security_scope, security_scope, profile_id + ':' + model,
                fingerprint([text_value, dimensions]))
            value = self.cache.get(key)
            if value is not None:
                result[i] = list(value)
            else:
                pending.append((i, text_value, key))
        for start in range(0, len(pending), self.batch_size):
            batch = pending[start:start+self.batch_size]
            response = self.client.request('POST', '/embeddings', body={'model': model,
                'input': [x[1] for x in batch], 'dimensions': dimensions})
            rows = sorted(response['data'], key=lambda row: row['index'])
            if [row['index'] for row in rows] != list(range(len(batch))):
                raise ValueError('embedding provider returned an invalid batch')
            for (i, _, key), row in zip(batch, rows):
                vector = row['embedding']
                if len(vector) != dimensions or any(not math.isfinite(float(v)) for v in vector):
                    raise ValueError('embedding provider returned invalid dimensions or values')
                result[i] = vector
                self.cache.put(key, tuple(vector), size_bytes=dimensions * 32)
        return result
