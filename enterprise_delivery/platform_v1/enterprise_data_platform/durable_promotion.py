"""One durable routing pointer atomically switches keyword, ANN and hydration."""
from sqlalchemy import select
from .control_models import IndexDeployment
from .control_state import ResourceNotFound
from .durable import objects
from .promotion import IndexPromotionController, PromotionBlocked, RetrievalQualityEvidence


class SQLIndexPromotionController(IndexPromotionController):
    def __init__(self, store, thresholds=None):
        super().__init__(thresholds)
        self.store = store

    def transition(self, index_id, action, body=None):
        body = body or {}
        initial = self.store.get('indexes', index_id)
        dataset = initial['payload']['dataset_id']
        with self.store.engine.begin() as conn:
            rows = conn.execute(select(objects).where(objects.c.kind == 'indexes', objects.c.deleted.is_(False),
                objects.c.payload['dataset_id'].as_string() == dataset).order_by(objects.c.id).with_for_update()).mappings().all()
            deployments = [IndexDeployment.model_validate({**r['payload'], 'revision': r['revision']}) for r in rows]
            if not deployments or len(deployments) > 2:
                raise PromotionBlocked('expected one coordinated keyword/vector deployment pair')
            signatures = {(d.active_version, d.candidate_version, d.state, d.traffic_to_candidate_percent) for d in deployments}
            if len(signatures) != 1:
                raise PromotionBlocked('keyword and vector deployment state must agree')
            saved = None
            for deployment in deployments:
                if action == 'validate':
                    updated = self.validate_candidate(deployment, RetrievalQualityEvidence(**body))
                elif action == 'canary':
                    updated = self.set_canary(deployment, int(body.get('percent', -1)))
                elif action == 'promote':
                    updated = self.promote(deployment)
                elif action == 'rollback-canary':
                    updated = self.rollback_canary(deployment)
                else:
                    raise ValueError('invalid promotion transition')
                revision = self.store.put('indexes', updated.id, updated.model_dump(mode='json'),
                    expected_revision=deployment.revision, conn=conn)
                if updated.id == index_id:
                    saved = updated.model_copy(update={'revision': revision})
            old_route = conn.execute(select(objects).where(objects.c.kind == 'routing', objects.c.id == dataset).with_for_update()).mappings().first()
            route = {'active_version': updated.active_version, 'candidate_version': updated.candidate_version,
                'traffic_percent': updated.traffic_to_candidate_percent}
            self.store.put('routing', dataset, route, expected_revision=old_route['revision'] if old_route else 0, conn=conn)
            self.store.audit('index.' + action, dataset, {'active_version': updated.active_version}, conn=conn)
            if saved is None:
                raise ResourceNotFound(index_id)
            return saved
