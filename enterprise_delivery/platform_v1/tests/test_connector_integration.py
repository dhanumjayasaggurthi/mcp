"""Optional real SQL/search contracts; CI provisions isolated services."""
import os
import uuid

import httpx
import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine

from enterprise_data_platform.backends import DeterministicHashEmbeddingProvider
from enterprise_data_platform.catalog import InMemoryCatalog
from enterprise_data_platform.chunks import SQLChunkStore
from enterprise_data_platform.connectors import ConnectorCapabilities, SQLConnector
from enterprise_data_platform.context import current_actor
from enterprise_data_platform.ingestion import IngestionBridge, IngestionEvent, IngestionWorker
from enterprise_data_platform.jobs import DurableQueue
from enterprise_data_platform.models import CountMode, DataProduct, FieldDefinition, SortField, SourceBinding
from enterprise_data_platform.search import OpenSearchBackend, OpenSearchSink, OpenSearchTransport, SearchSettings, index_name
from test_durable_security import store
from test_platform_core import product

pytestmark=pytest.mark.integration


def test_mysql_mariadb_null_keysets_and_parameterized_aggregates():
    dsn=os.getenv('EDP_TEST_SQL_DSN')
    if not dsn:
        pytest.skip('EDP_TEST_SQL_DSN is not set')
    # CI service is isolated. Production factory additionally requires verified TLS.
    engine=create_engine(dsn,pool_size=2,max_overflow=0,pool_timeout=1)
    name='test_'+uuid.uuid4().hex
    md=MetaData();table=Table(name,md,Column('id',Integer,primary_key=True,autoincrement=False),Column('tenant',String(64)),Column('value',Integer))
    md.create_all(engine)
    try:
        with engine.begin() as conn:
            conn.execute(table.insert(),[{'id':i,'tenant':'a' if i<5 else 'b','value':v} for i,v in enumerate([None,2,1,None,2,999])])
        p=DataProduct(id='test-data',display_name='Test',version='1',source=SourceBinding(connector='test',environment='test',object_name=name),
            identity_fields=['id'],fields=[FieldDefinition(name='id',data_type='int',sortable=True),
                FieldDefinition(name='tenant',data_type='string',filterable=True),FieldDefinition(name='value',data_type='int',sortable=True)])
        connector=SQLConnector(engine,ConnectorCapabilities(aggregation=True,native_nulls_last=False),timeout_seconds=2)
        predicate={'field':'tenant','op':'eq','value':'a'}
        for direction, expected in [('asc',[2,1,4,0,3]),('desc',[1,4,2,0,3])]:
            rows=[];position=None
            while True:
                page=connector.query(product=p,fields=['id','value'],filter_expr=predicate,
                    order_by=[SortField(field='value',direction=direction),SortField(field='id')],limit=2,position=position,count_mode=CountMode.NONE)
                rows.extend(r['id'] for r in page.rows)
                if not page.next_position: break
                position=page.next_position
            assert rows==expected
        from enterprise_data_platform.aggregation import AggregateRequest, Measure
        result=connector.aggregate(product=p,request=AggregateRequest(measures=[Measure(name='total',function='sum',field='value')]),filter_expr=predicate,limit=2)
        assert result[0]['total']==5
        injected=connector.query(product=p,fields=['id'],filter_expr={'field':'tenant','op':'eq','value':"a' OR 1=1 --"},
            order_by=[SortField(field='id')],limit=2,position=None,count_mode=CountMode.NONE)
        assert not injected.rows
    finally:
        md.drop_all(engine);engine.dispose()


def test_opensearch_real_bulk_filtered_ann_hydration_and_tombstone(store):
    endpoint=os.getenv('EDP_TEST_SEARCH_URL')
    if not endpoint:
        pytest.skip('EDP_TEST_SEARCH_URL is not set')
    client=httpx.Client(base_url=endpoint,timeout=30)
    transport=OpenSearchTransport('https://isolated-ci.example',client=client,timeout=30)
    p=product();p.id='search-'+uuid.uuid4().hex
    catalog=InMemoryCatalog();catalog.put(p)
    settings=SearchSettings(shards=1,replicas=0,refresh_interval='1s')
    keyword,vector=OpenSearchSink(transport,'keyword',settings),OpenSearchSink(transport,'vector',settings)
    version=p.retrieval.vector.index_version
    queue=DurableQueue(store);chunks=SQLChunkStore(store);embedder=DeterministicHashEmbeddingProvider()
    worker=IngestionWorker(queue=queue,catalog=catalog,chunk_store=chunks,keyword_sink=keyword,vector_sink=vector,embedder=embedder)
    try:
        keyword.provision(p,version);vector.provision(p,version)
        def event(tenant,sequence,operation='upsert'):
            return IngestionEvent(dataset_id=p.id,dataset_version=p.version,index_version=version,partition=tenant,
                sequence=sequence,event_id=tenant+str(sequence),operation=operation,record_id=tenant,source_version=str(sequence),
                values={'id':tenant,'tenant_id':tenant,'title':'Title','body':'clear synthetic document'})
        bridge=IngestionBridge(queue)
        for tenant in ['acme','other']:
            bridge.publish(event(tenant,1));worker.process(queue.claim('ingestion'))
        token=current_actor.set({'subject':'reader','groups':[],'tenant':'acme'})
        try:
            predicate={'field':'tenant_id','op':'eq','value':'acme'}
            hits=OpenSearchBackend(transport,'keyword').search(product=p,query='synthetic',filter_expr=predicate,top_k=3)
            assert [h.record_id for h in hits]==['acme']
            assert chunks.get_chunks(product=p,chunk_ids=[h.chunk_id for h in hits])
            vector_hits=OpenSearchBackend(transport,'vector').search(product=p,vector=[.1]*16,filter_expr=predicate,top_k=3)
            assert vector_hits and all(h.record_id=='acme' for h in vector_hits)
            bridge.publish(event('acme',2,'delete'));worker.process(queue.claim('ingestion'))
            assert not OpenSearchBackend(transport,'keyword').search(product=p,query='synthetic',filter_expr=predicate,top_k=3)
            assert not chunks.get_chunks(product=p,chunk_ids=[h.chunk_id for h in hits])
        finally:
            current_actor.reset(token)
    finally:
        for kind in ['keyword','vector']:
            client.delete('/'+index_name(p.id,version,kind))
        client.close()
