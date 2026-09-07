from enterprise_data_platform.models import RetrievalHit
from enterprise_data_platform.quality import GoldenQueryResult, evaluate_retrieval


def hit(record_id, cited=True):
    return RetrievalHit(record_id=record_id, source={"dataset": "d", "record_id": record_id} if cited else {})


def test_quality_metrics_are_computed_from_golden_queries():
    evidence = evaluate_retrieval([
        GoldenQueryResult("q1", {"1", "2"}, [hit("1"), hit("2"), hit("9")], 100),
        GoldenQueryResult("q2", {"3"}, [hit("3"), hit("8", cited=False)], 200),
    ], k=3)
    assert evidence.evaluated_queries == 2
    assert evidence.recall_at_k == 1.0
    assert 0.5 < evidence.precision_at_k < 1.0
    assert evidence.citation_coverage == 4 / 5
    assert evidence.p95_latency_ms == 200
    assert evidence.error_rate == 0
