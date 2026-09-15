"""Native search over existing PostgreSQL chunk tables; source rows are canonical.

All user values are bind parameters. Administrator-owned mappings are resolved as
SQLAlchemy columns. Search, policy filtering and canonical hydration happen in
one statement, so no independently replicated search index is required.
"""
from __future__ import annotations

import json
import math

from sqlalchemy import String, Float, Text, and_, bindparam, cast, func, or_, select, text, literal_column
from sqlalchemy.dialects.postgresql import REGCONFIG, ARRAY
from sqlalchemy.types import UserDefinedType

from .context import current_actor, current_context
from .models import RetrievalHit
from .services import CapabilityUnavailable


class VectorType(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions, schema='public'):
        self.dimensions, self.schema = dimensions, schema

    def get_col_spec(self, **kw):
        # Both values come from validated administrator configuration.
        return f'"{self.schema}".vector({int(self.dimensions)})'


def native(product):
    return bool(product.retrieval and product.retrieval.backend == 'postgres')


class PostgresRetrievalBackend:
    def __init__(self, router, kind):
        self.router, self.kind = router, kind

    def search(self, *, product, filter_expr, top_k, query=None, vector=None):
        connector = self.router.resolve(product)
        if not hasattr(connector, 'engine') or connector.engine.dialect.name != 'postgresql':
            raise CapabilityUnavailable('native retrieval requires a PostgreSQL source')
        mapping = product.retrieval.postgres
        if not mapping:
            raise ValueError('PostgreSQL retrieval mapping is missing')
        if not 1 <= top_k <= product.max_top_k:
            raise ValueError('top-k exceeds dataset budget')
        table = connector.backend._table(product, connector.engine)
        predicate = connector.backend._compile_filter(table, filter_expr) if filter_expr else True
        actor = current_actor.get() or {}
        constraints = [predicate]
        for name, identities in [(mapping.acl_subjects_field, [actor.get('subject', '')]),
                                 (mapping.acl_groups_field, actor.get('groups', []))]:
            if name:
                col = table.c[name]
                if not isinstance(col.type, ARRAY):
                    raise ValueError('native ACL field must be a PostgreSQL array')
                constraints.append(or_(col.is_(None), func.cardinality(col) == 0, col.overlap(identities)))
        metadata_fields = {f.name for f in product.fields if f.filterable or f.vector_metadata} | set(mapping.citation_fields.values())
        selected = metadata_fields | {mapping.chunk_id_field, mapping.record_id_field, mapping.text_field}
        selected.update(x for x in [mapping.source_version_field, mapping.acl_subjects_field, mapping.acl_groups_field] if x)
        selected.update(mapping.citation_fields.values())
        metadata_fields.discard(mapping.vector_field)
        metadata_fields.discard(mapping.keyword_tsvector_field)
        # Embeddings are never returned, even when mistakenly marked as metadata.
        selected.discard(mapping.vector_field)
        selected.discard(mapping.keyword_tsvector_field)
        columns = [table.c[name] for name in sorted(selected)]
        statement, score_kind = self.statement(product, table, columns, constraints, query, vector, top_k)
        context = current_context.get()
        seconds = min(connector.backend.timeout_seconds, context.remaining() if context else connector.backend.timeout_seconds)
        with connector.engine.connect() as conn, conn.begin():
            conn.execute(text('SET TRANSACTION READ ONLY'))
            conn.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                         {'timeout': str(max(1, int(seconds * 1000)))})
            if self.kind == 'vector':
                if mapping.iterative_scan:
                    version = conn.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar()
                    if not version or tuple(int(v) for v in version.split('.')[:2]) < (0, 8):
                        raise CapabilityUnavailable('filtered iterative ANN requires pgvector >= 0.8')
                    conn.execute(text("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)"))
                    conn.execute(text("SELECT set_config('hnsw.max_scan_tuples', :v, true)"), {'v': str(mapping.hnsw_max_scan_tuples)})
                conn.execute(text("SELECT set_config('hnsw.ef_search', :v, true)"), {'v': str(max(top_k, mapping.hnsw_ef_search))})
            connector.backend._check_scan(conn, statement)
            rows = conn.execute(statement).mappings().all()
        if context: context.remaining()
        hits = []
        for rank, row in enumerate(rows, 1):
            score = float(row['_retrieval_score'])
            if not math.isfinite(score): continue
            metadata = {k: row[k] for k in metadata_fields if k in row}
            chunk_id, record_id = str(row[mapping.chunk_id_field]), str(row[mapping.record_id_field])
            version = str(row[mapping.source_version_field]) if mapping.source_version_field and row[mapping.source_version_field] is not None else None
            acl = {kind: row[name] or [] for kind, name in [('subjects', mapping.acl_subjects_field),
                    ('groups', mapping.acl_groups_field)] if name}
            canonical = {'record_id': record_id, 'chunk_id': chunk_id, 'dataset_version': product.version,
                         'text': row[mapping.text_field], 'metadata': metadata, 'source_version': version, 'acl': acl}
            hits.append(RetrievalHit(record_id=record_id, chunk_id=chunk_id, score=score,
                scores={self.kind: score}, ranks={self.kind: rank}, canonical=canonical,
                source={'score_kind': score_kind}))
        return hits

    def statement(self, product, table, columns, constraints, query, vector, top_k):
        mapping = product.retrieval.postgres
        chunk_id = table.c[mapping.chunk_id_field]
        if self.kind == 'keyword':
            if mapping.keyword_mode == 'contains':
                if not query or len(query.strip()) < 3:
                    raise ValueError('contains retrieval requires at least three characters')
                from .sqlalchemy_backend import SQLAlchemyStructuredBackend
                match = table.c[mapping.text_field].ilike('%' + SQLAlchemyStructuredBackend._escape_like(query) + '%', escape='\\')
                return select(*columns, literal_column('1.0').label('_retrieval_score')).where(
                    and_(True, *constraints), match).order_by(chunk_id).limit(top_k), 'postgres_literal_contains'
            # Validated administrator regconfig is a SQL constant so prepared
            # generic plans can match the expression GIN index. Query text stays bound.
            config = literal_column("'" + mapping.text_search_config + "'::regconfig", type_=REGCONFIG)
            document = table.c[mapping.keyword_tsvector_field] if mapping.keyword_tsvector_field else func.to_tsvector(config, table.c[mapping.text_field])
            tsquery = func.websearch_to_tsquery(config, bindparam('search_query', query, type_=String()))
            score = func.ts_rank_cd(document, tsquery, 32)
            statement = select(*columns, score.label('_retrieval_score')).where(
                and_(True, *constraints), document.bool_op('@@')(tsquery)).order_by(score.desc(), chunk_id).limit(top_k)
            return statement, 'postgres_ts_rank_cd'
        profile = product.retrieval.vector
        if not profile or not mapping.vector_field or vector is None:
            raise ValueError('vector search requires a registered vector profile and column')
        if len(vector) != profile.dimensions or any(not math.isfinite(float(v)) for v in vector):
            raise ValueError('invalid query embedding')
        if profile.distance == 'cosine' and not any(float(v) != 0 for v in vector):
            raise ValueError('cosine query vector cannot be zero')
        vtype = VectorType(profile.dimensions, mapping.vector_schema)
        query_vector = cast(bindparam('query_embedding', json.dumps([float(v) for v in vector], allow_nan=False), type_=Text()), vtype)
        column = table.c[mapping.vector_field]
        indexed_column = cast(column, vtype) if mapping.cast_vector else column
        operator = {'cosine': '<=>', 'dot': '<#>', 'l2': '<->'}[profile.distance]
        distance = indexed_column.op(operator, return_type=Float())(query_vector)
        # Direct ascending distance ORDER BY allows HNSW index scans. The outer
        # sort deterministically orders the bounded candidate set without
        # changing the inner index-compatible ordering expression.
        score = 1 - distance if profile.distance == 'cosine' else -distance
        candidates = select(*columns, score.label('_retrieval_score'), distance.label('_distance')).where(
            and_(True, *constraints), column.is_not(None)).order_by(distance).limit(top_k).subquery()
        return select(candidates).order_by(candidates.c._distance, candidates.c[mapping.chunk_id_field]), 'pgvector_' + profile.distance


class RetrievalBackendRouter:
    def __init__(self, source_router, fallback, kind):
        self.postgres = PostgresRetrievalBackend(source_router, kind)
        self.fallback = fallback

    def search(self, **kwargs):
        if native(kwargs['product']):
            return self.postgres.search(**kwargs)
        if self.fallback is None:
            raise CapabilityUnavailable('OpenSearch backend is not configured')
        return self.fallback.search(**kwargs)
