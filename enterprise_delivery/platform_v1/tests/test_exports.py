from enterprise_data_platform.exports import QueuedExportBackend
from enterprise_data_platform.models import Capability, ExportRequest
from enterprise_data_platform.services import PlatformService
from test_platform_core import product, principal
from enterprise_data_platform.catalog import InMemoryCatalog
from enterprise_data_platform.cursor import CursorCodec
from enterprise_data_platform.models import AccessPolicy, PolicyEffect
from enterprise_data_platform.policy import PolicyEngine


def test_export_submission_is_queue_only_not_browser_or_api_materialization():
    p = product().model_copy(update={"capabilities": product().capabilities | {Capability.EXPORT}})
    catalog = InMemoryCatalog(); catalog.put(p)
    policies = PolicyEngine([AccessPolicy(
        id="export",
        effect=PolicyEffect.ALLOW,
        dataset_patterns=[p.id],
        operations={Capability.EXPORT},
        client_ids={"regassist-prod"},
        allowed_fields={"id", "tenant_id", "title", "body"},
        require_tenant_isolation=True,
    )])
    queued = []
    exporter = QueuedExportBackend(lambda job_id, payload: queued.append((job_id, payload)))
    service = PlatformService(catalog=catalog, policies=policies, cursor_codec=CursorCodec(b"e" * 32), exporter=exporter)
    job = service.export(principal(), p.id, ExportRequest(select=["id", "title"], format="parquet"))
    assert job.status.value == "queued"
    assert len(queued) == 1
    assert queued[0][1]["filter"] == {"field": "tenant_id", "op": "eq", "value": "acme"}
    assert queued[0][1]["format"] == "parquet"
