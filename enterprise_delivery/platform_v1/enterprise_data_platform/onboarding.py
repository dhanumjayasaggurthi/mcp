"""Administrator-only source inspection and reproducible dataset contract drafts.

These operations read source metadata. They never execute source DDL or grant
consumer access. Activation validates the physical binding; policy/client grants
remain separate, explicit control-plane operations.
"""
from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace
from typing import Literal

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import inspect, text

from .models import (Capability, DataProduct, FieldDefinition, FieldPolicy,
                     PostgresRetrievalProfile, ProductStatus, RetrievalProfile,
                     SourceBinding, TextProfile, VectorProfile)
from .query_validation import field_family, field_operators


class InspectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_name: str | None = Field(default=None, max_length=128)
    object_name: str = Field(min_length=1, max_length=128)


class DraftDatasetRequest(InspectRequest):
    source_id: str
    dataset_id: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]{1,127}$')
    display_name: str = Field(min_length=1, max_length=256)
    version: str = '1'
    environment: str = 'prod'
    template: Literal['table', 'postgres_chunks'] = 'table'
    vector_profile: VectorProfile | None = None
    postgres: PostgresRetrievalProfile | None = None
    tenant_field: str | None = None
    mandatory_filter: dict | None = None


def binding(router, source_id, body):
    reg = router.registry.get(source_id)
    return SourceBinding(connector=reg.kind, source_id=reg.id, environment='inspection',
                         schema_name=body.schema_name, object_name=body.object_name)


def inspect_object(router, source_id, body):
    product = SimpleNamespace(source=binding(router, source_id, body))
    connector = router.resolve(product)
    if not hasattr(connector, 'engine'):
        raise ValueError('source inspection requires a SQL connector; provide a contract for REST sources')
    with connector.engine.connect() as conn:
        ins = inspect(conn)
        columns = ins.get_columns(body.object_name, schema=body.schema_name)
        if len(columns) > 512: raise ValueError('source object exceeds 512-column contract limit')
        primary = ins.get_pk_constraint(body.object_name, schema=body.schema_name).get('constrained_columns') or []
        indexes = ins.get_indexes(body.object_name, schema=body.schema_name)
        unique = ins.get_unique_constraints(body.object_name, schema=body.schema_name)
        result = {'source_id': source_id, 'schema_name': body.schema_name, 'object_name': body.object_name,
            'dialect': conn.dialect.name,
            'columns': [{'name': c['name'], 'data_type': str(c['type']).lower(), 'nullable': c['nullable'],
                         'filter_operators': sorted(field_operators(str(c['type'])))} for c in columns],
            'primary_key': primary,
            'unique_keys': [x['column_names'] for x in unique] + [x['column_names'] for x in indexes if x.get('unique')],
            'indexes': [{'name': x['name'], 'columns': x.get('column_names', []),
                         'expressions': x.get('expressions', []), 'unique': bool(x.get('unique')),
                         'method': x.get('dialect_options', {}).get('postgresql_using')} for x in indexes],
            'connector_capabilities': asdict(connector.capabilities)}
        if conn.dialect.name == 'postgresql':
            # SQLAlchemy may not recognize extension types. Read declared type
            # metadata directly, without reading corpus rows or embeddings.
            rows = conn.execute(text("""SELECT a.attname, pg_catalog.format_type(a.atttypid,a.atttypmod) AS type
                FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=COALESCE(:schema,current_schema()) AND c.relname=:object
                AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"""),
                {'schema': body.schema_name, 'object': body.object_name}).mappings()
            types = {r['attname']: r['type'] for r in rows}
            for c in result['columns']:
                c['data_type'] = types[c['name']]
                c['filter_operators'] = sorted(field_operators(c['data_type']))
            # Concurrent builds can leave invalid indexes. Partial indexes do
            # not cover arbitrary authorized filters, so do not count them.
            details = conn.execute(text("""SELECT idx.relname AS name,
                pg_get_indexdef(i.indexrelid) AS definition,
                i.indisvalid AND i.indisready AND i.indpred IS NULL AS usable
                FROM pg_index i JOIN pg_class tbl ON tbl.oid=i.indrelid
                JOIN pg_namespace ns ON ns.oid=tbl.relnamespace
                JOIN pg_class idx ON idx.oid=i.indexrelid
                WHERE ns.nspname=COALESCE(:schema,current_schema()) AND tbl.relname=:object"""),
                {'schema': body.schema_name, 'object': body.object_name}).mappings()
            by_name = {row['name']: dict(row) for row in details}
            for index in result['indexes']:
                index.update(by_name.get(index['name'], {'usable': False}))
            result['pgvector_version'] = conn.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar()
    return result


def draft_dataset(router, body):
    report = inspect_object(router, body.source_id, body)
    if not report['primary_key']:
        raise ValueError('automatic onboarding requires a primary key; register an explicit unique identity for a view')
    fields = []
    for c in report['columns']:
        family = field_family(c['data_type'])
        opaque = family == 'opaque'
        # Internal embeddings/tsvectors never become consumer projections.
        fields.append(FieldDefinition(name=c['name'], data_type=c['data_type'], nullable=c['nullable'],
            selectable=not opaque, filterable=bool(c['filter_operators']),
            sortable=family not in {'opaque', 'array', 'json'},
            default_policy=FieldPolicy.HIDDEN if opaque else FieldPolicy.VISIBLE))
    capabilities = {Capability.QUERY}
    retrieval = None
    if body.template == 'postgres_chunks':
        if report['dialect'] != 'postgresql': raise ValueError('postgres_chunks requires PostgreSQL')
        names = {f.name for f in fields}
        mapping = body.postgres or PostgresRetrievalProfile(
            source_version_field='updated_at' if 'updated_at' in names else None,
            citation_fields={k: k for k in ['file_name', 'section_title', 'page_start', 'page_end'] if k in names})
        for f in fields:
            if f.name in mapping.citation_fields.values(): f.vector_metadata = True
            if f.name in {mapping.acl_subjects_field, mapping.acl_groups_field}:
                f.selectable = False
                f.default_policy = FieldPolicy.HIDDEN
        capabilities |= {Capability.KEYWORD, Capability.RETRIEVE}
        if body.vector_profile:
            capabilities |= {Capability.VECTOR, Capability.HYBRID}
        retrieval = RetrievalProfile(backend='postgres', postgres=mapping,
            keyword_index=body.version, vector=body.vector_profile,
            text=TextProfile(source_fields=[mapping.text_field]),
            deduplicate_content=False, max_chunks_per_record=100)
    product = DataProduct(id=body.dataset_id, display_name=body.display_name, version=body.version,
        status=ProductStatus.DRAFT, source=binding(router, body.source_id, body).model_copy(update={'environment': body.environment}),
        identity_fields=report['primary_key'], fields=fields, capabilities=capabilities,
        retrieval=retrieval, tenant_field=body.tenant_field, mandatory_filter=body.mandatory_filter)
    product.validate_contract()
    return {'dataset': product.model_dump(mode='json'), 'source': report,
            'next_steps': ['Review fields and dataset-wide mandatory filter',
                           'Validate physical binding and provision required indexes',
                           'PUT dataset contract; explicitly activate after validation',
                           'Register consumer client and least-privilege policy']}


def validate_binding(router, product):
    product.validate_contract()
    report = inspect_object(router, product.source.source_id or product.source.connector,
                            InspectRequest(schema_name=product.source.schema_name, object_name=product.source.object_name))
    columns = {c['name']: c for c in report['columns']}
    issues, notes = [], []
    for f in product.fields:
        if f.name not in columns:
            issues.append(f'missing source field: {f.name}')
        elif field_family(f.data_type) != field_family(columns[f.name]['data_type']):
            issues.append(f'incompatible source field type: {f.name}')
    unique_keys = [report['primary_key'], *report['unique_keys']]
    if not any(set(key) == set(product.identity_fields) for key in unique_keys):
        issues.append('identity fields must match a source primary key or unique key')
    if any(columns.get(f, {}).get('nullable', True) for f in product.identity_fields):
        issues.append('identity fields must be non-nullable')
    if product.retrieval and product.retrieval.backend == 'postgres':
        import re
        mapping = product.retrieval.postgres
        if report['dialect'] != 'postgresql': issues.append('native retrieval requires PostgreSQL')
        index_text = ' '.join(' '.join(str(c) for c in [*x['columns'], *x['expressions']]) for x in report['indexes'] if x['method'] == 'gin' and x.get('usable', True))
        if mapping.keyword_tsvector_field:
            if columns.get(mapping.keyword_tsvector_field, {}).get('data_type', '').split('.')[-1] != 'tsvector':
                issues.append('keyword_tsvector_field must be a tsvector column')
            if mapping.keyword_tsvector_field not in index_text:
                issues.append('keyword search requires a GIN index on the configured tsvector column')
        elif not any(x['method'] == 'gin' and x.get('usable', True) and
                'to_tsvector' in str(x['expressions']) and mapping.text_field in str(x['expressions']) and
                mapping.text_search_config.split('.')[-1] in str(x['expressions']) for x in report['indexes']):
            issues.append('keyword search requires a matching to_tsvector GIN index; a trigram index is not full-text indexing')
        if product.retrieval.vector:
            profile = product.retrieval.vector
            declared = columns.get(mapping.vector_field, {}).get('data_type', '')
            match = re.search(r'vector\((\d+)\)', declared)
            if 'vector' not in declared: issues.append('configured embedding column is not a vector')
            if match and int(match[1]) != profile.dimensions: issues.append('source embedding dimension does not match profile')
            if not match and not mapping.cast_vector:
                issues.append('unbounded vector column requires cast_vector=true and a matching dimension-specific HNSW expression index')
            ops = {'cosine': 'vector_cosine_ops', 'dot': 'vector_ip_ops', 'l2': 'vector_l2_ops'}[profile.distance]
            if not any(x['method'] == 'hnsw' and x.get('usable', True) and
                    mapping.vector_field in str(x['columns'] + x['expressions']) and
                    ops in x.get('definition', '') and
                    (not mapping.cast_vector or f'vector({profile.dimensions})' in x.get('definition', ''))
                    for x in report['indexes']):
                issues.append('vector search requires a matching HNSW index')
            version = report.get('pgvector_version')
            if mapping.iterative_scan and (not version or tuple(int(v) for v in version.split('.')[:2]) < (0, 8)):
                issues.append('filtered iterative ANN requires pgvector >= 0.8')
            notes.append('Verify source embeddings use the configured immutable model; dimensions alone do not prove model compatibility')
        for name in [mapping.acl_subjects_field, mapping.acl_groups_field]:
            if name and field_family(columns.get(name, {}).get('data_type', '')) != 'array':
                issues.append('ACL mapping requires array fields')
        notes.append('Business approval must use an authoritative field/view; ingestion status is not document approval')
    return {'valid': not issues, 'issues': issues, 'notes': notes, 'source': report}


def register_onboarding(app, runtime):
    admin = app.state.admin_dependency

    @app.post('/v1/control/sources/{source_id}/check')
    def check_source(source_id: str, principal=Depends(admin)):
        import time
        from .durable import now_iso
        from fastapi import HTTPException
        start = time.monotonic()
        source = binding(runtime.router, source_id, InspectRequest(object_name='connection_check'))
        connector = runtime.router.resolve(SimpleNamespace(source=source))
        if not connector.health():
            raise HTTPException(503, 'Source did not pass its connection check')
        return {'source_id': source_id, 'status': 'reachable', 'checked_at': now_iso(),
                'elapsed_ms': round((time.monotonic() - start) * 1000, 2)}

    @app.get('/v1/control/sources/{source_id}/objects')
    def objects(source_id: str, schema_name: str | None = None, limit: int = Query(100, ge=1, le=200), principal=Depends(admin)):
        source = binding(runtime.router, source_id, InspectRequest(schema_name=schema_name, object_name='discovery'))
        connector = runtime.router.resolve(SimpleNamespace(source=source))
        if not hasattr(connector, 'engine'): raise ValueError('source is not SQL')
        with connector.engine.connect() as conn:
            if conn.dialect.name == 'postgresql':
                rows = conn.execute(text("""SELECT c.relname AS object_name, n.nspname AS schema_name,
                    CASE WHEN c.relkind IN ('v','m') THEN 'view' ELSE 'table' END AS kind
                    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE c.relkind IN ('r','p','v','m') AND n.nspname=COALESCE(:schema,current_schema())
                    ORDER BY c.relname LIMIT :limit"""), {'schema': schema_name, 'limit': limit + 1}).mappings().all()
                values = [dict(x) for x in rows]
            else:
                ins = inspect(conn)
                values = [{'object_name': n, 'schema_name': schema_name, 'kind': 'table'} for n in ins.get_table_names(schema=schema_name)[:limit+1]]
        return {'objects': values[:limit], 'truncated': len(values) > limit}

    @app.post('/v1/control/sources/{source_id}/inspect')
    def inspect_source(source_id: str, body: InspectRequest, principal=Depends(admin)):
        return inspect_object(runtime.router, source_id, body)

    @app.post('/v1/control/onboarding/preview')
    def preview(body: DraftDatasetRequest, principal=Depends(admin)):
        return draft_dataset(runtime.router, body)

    @app.post('/v1/control/onboarding/validate')
    def validate(body: DataProduct, principal=Depends(admin)):
        return validate_binding(runtime.router, body)

    @app.post('/v1/control/onboarding/index-plan')
    def indexes(body: DataProduct, principal=Depends(admin)):
        return index_plan(body)

    def activation_validator(product):
        if product.status != ProductStatus.ACTIVE: return
        # REST contracts use their own governed protocol; there is no generic
        # SQL introspector for them. Their existing explicit registration stays.
        if product.source.connector == 'rest': return
        result = validate_binding(runtime.router, product)
        if not result['valid']: raise ValueError('; '.join(result['issues']))
    app.state.dataset_validator = activation_validator


def index_plan(product):
    """Reviewable online DDL; never executed by the API's read-only source role."""
    from sqlalchemy.dialects.postgresql import dialect
    from .durable import fingerprint
    if not product.retrieval or product.retrieval.backend != 'postgres':
        raise ValueError('native index plans require a PostgreSQL retrieval product')
    product.validate_contract()
    mapping = product.retrieval.postgres
    quote = dialect().identifier_preparer.quote_identifier
    table = (quote(product.source.schema_name) + '.' if product.source.schema_name else '') + quote(product.source.object_name)
    prefix = 'rdh_' + fingerprint([product.id, product.version])[:16]
    cfg = mapping.text_search_config
    tsvector = quote(mapping.keyword_tsvector_field) if mapping.keyword_tsvector_field else f"to_tsvector('{cfg}'::regconfig, {quote(mapping.text_field)})"
    statements = [f'CREATE INDEX CONCURRENTLY {quote(prefix + "_fts")} ON {table} USING gin ({tsvector});']
    ordering = [mapping.record_id_field]
    if 'chunk_index' in product.field_map(): ordering.append('chunk_index')
    ordering.append(mapping.chunk_id_field)
    statements.append(f'CREATE INDEX CONCURRENTLY {quote(prefix + "_document")} ON {table} ({", ".join(quote(n) for n in dict.fromkeys(ordering))});')
    if product.retrieval.vector:
        profile = product.retrieval.vector
        column = quote(mapping.vector_field)
        if mapping.cast_vector: column = f'({column}::{quote(mapping.vector_schema)}.vector({profile.dimensions}))'
        ops = {'cosine': 'vector_cosine_ops', 'dot': 'vector_ip_ops', 'l2': 'vector_l2_ops'}[profile.distance]
        statements.append(f'CREATE INDEX CONCURRENTLY {quote(prefix + "_hnsw")} ON {table} USING hnsw ({column} {quote(mapping.vector_schema)}.{ops}) WITH (m=16, ef_construction=128);')
    statements.append(f'ANALYZE {table};')
    return {'dataset_id': product.id, 'statements': statements, 'executed': False,
            'instructions': ['Use a source-owner DDL role; run each statement outside a transaction',
                             'Check existing equivalent indexes and invalid indexes before executing',
                             'Verify embedding dimensions and model before building HNSW',
                             'Validate again after index creation; measure representative filtered retrieval'],
            'existing_data': 'unchanged; generated indexes are additional'}
