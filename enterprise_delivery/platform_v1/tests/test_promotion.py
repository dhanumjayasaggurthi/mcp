import pytest

from enterprise_data_platform.control_models import IndexDeployment
from enterprise_data_platform.promotion import IndexPromotionController, PromotionBlocked, RetrievalQualityEvidence


def deployment():
    return IndexDeployment(
        id="docs-vector",
        dataset_id="docs",
        index_type="vector",
        active_version="v1",
        candidate_version="v2",
        state="building",
        freshness_lag_seconds=15,
        indexed_records=2_000_000_000,
        shard_count=128,
        replica_count=2,
    )


def good_evidence():
    return RetrievalQualityEvidence(
        evaluated_queries=500,
        recall_at_k=.95,
        precision_at_k=.88,
        citation_coverage=1.0,
        p95_latency_ms=700,
        error_rate=0.0001,
    )


def test_zero_downtime_promotion_requires_quality_then_full_canary():
    c = IndexPromotionController()
    d = c.validate_candidate(deployment(), good_evidence())
    assert d.active_version == "v1" and d.candidate_version == "v2"
    d = c.set_canary(d, 10)
    assert d.active_version == "v1" and d.traffic_to_candidate_percent == 10
    with pytest.raises(PromotionBlocked):
        c.promote(d)
    d = c.set_canary(d, 100)
    promoted = c.promote(d)
    assert promoted.active_version == "v2"
    assert promoted.candidate_version is None
    assert promoted.state == "healthy"


def test_accuracy_gate_blocks_bad_candidate_without_touching_active_version():
    c = IndexPromotionController()
    d = deployment()
    bad = RetrievalQualityEvidence(500, .4, .8, 1.0, 500, 0.0001)
    with pytest.raises(PromotionBlocked, match="recall_at_k"):
        c.validate_candidate(d, bad)
    assert d.active_version == "v1"
