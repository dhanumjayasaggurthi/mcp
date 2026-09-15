"""Isolated browser-test API. Never imported by a production entry point."""
import os
if os.getenv('EDP_UI_TEST_MODE') != 'true':
    raise RuntimeError('UI test mode must be explicitly enabled')
import tempfile
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import create_engine
from enterprise_data_platform.api import create_app,_register_registry_routes
from enterprise_data_platform.durable import RelationalStore,migrate
from enterprise_data_platform.connectors import SourceRegistration
from enterprise_data_platform.control_models import AgentRegistration
from enterprise_data_platform.production_app import SQLOperationsProvider
from enterprise_data_platform.onboarding import register_onboarding
from enterprise_data_platform.observability import Metrics
from enterprise_data_platform.jobs import DurableQueue
from test_production_boundaries import runtime_service
root=tempfile.TemporaryDirectory(prefix='control-hub-test-')
engine=create_engine('sqlite:///'+root.name+'/control.db',connect_args={'check_same_thread':False})
migrate(engine)
store=RelationalStore(engine,production=False)
service,actor=runtime_service(store)
service.metrics=Metrics()
actor=actor.model_copy(update={'groups':{'data-platform-admin'}})
def resolver(request):
    if request.headers.get('authorization') != 'Bearer ui-test-only':
        raise HTTPException(401,'test identity required')
    return actor
app=create_app(service=service,catalog=service.catalog,policies=service.policies,control_state=service.control,
    principal_resolver=resolver,control_admin_check=lambda p:'data-platform-admin' in p.groups,
    operations_provider=SQLOperationsProvider(store,service.metrics))
router=service.structured
_register_registry_routes(app,'sources',router.registry,SourceRegistration,app.state.admin_dependency)
register_onboarding(app,SimpleNamespace(router=router))
queue=DurableQueue(store)
queue.enqueue('ingestion','ui-test',{'test_fixture':True})
store.audit('ui.test.ready','facts',{'fixture':True})
