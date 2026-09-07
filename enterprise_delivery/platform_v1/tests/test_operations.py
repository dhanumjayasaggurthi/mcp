from concurrent.futures import ThreadPoolExecutor

from enterprise_data_platform.operations import InMemoryOperationsProvider


def test_operational_snapshot_is_isolated_and_concurrency_safe():
    provider = InMemoryOperationsProvider({"services": [{"name": "vector", "qps": "15K"}]})

    with ThreadPoolExecutor(max_workers=32) as executor:
        snapshots = list(executor.map(lambda _: provider.snapshot(), range(1000)))

    snapshots[0]["services"][0]["qps"] = "corrupt"
    assert provider.snapshot()["services"][0]["qps"] == "15K"
    assert all(snapshot["generated_at"].endswith("+00:00") for snapshot in snapshots)


def test_operational_snapshot_is_bounded_aggregate_not_raw_records():
    provider = InMemoryOperationsProvider({"metrics": {"indexed_records": 7_400_000_000}})
    snapshot = provider.snapshot()
    assert snapshot["metrics"]["indexed_records"] == 7_400_000_000
    assert "records" not in snapshot
