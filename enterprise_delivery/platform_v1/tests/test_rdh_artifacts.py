import json
from pathlib import Path

import yaml

from enterprise_data_platform.connectors import SourceRegistration
from enterprise_data_platform.control_models import ClientRegistration
from enterprise_data_platform.models import (
    AccessPolicy,
    LookupRequest,
    SearchRequest,
    StructuredQueryRequest,
    VectorSearchRequest,
)
from enterprise_data_platform.onboarding import DraftDatasetRequest


ROOT = Path(__file__).parents[1]
EXAMPLES = ROOT / "examples" / "rdh"


def load(name):
    return json.loads((EXAMPLES / name).read_text())


def test_rdh_consumer_and_control_examples_match_runtime_models():
    SourceRegistration.model_validate(load("source-registration.json"))
    ClientRegistration.model_validate(load("client-registration.json"))
    AccessPolicy.model_validate(load("read-policy.json"))
    DraftDatasetRequest.model_validate(load("clinical-preview.json"))
    StructuredQueryRequest.model_validate(load("query-request.json"))
    StructuredQueryRequest.model_validate(load("messages-query.json"))
    LookupRequest.model_validate(load("lookup-request.json"))
    SearchRequest.model_validate(load("keyword-request.json"))
    vector = VectorSearchRequest.model_validate(load("vector-request.json"))
    vector.validate_one_input()


def test_postgres_native_overlay_removes_worker_deployments_and_budgets():
    overlay = yaml.safe_load(
        (ROOT / "deploy" / "kubernetes" / "postgres-native" / "kustomization.yaml").read_text()
    )
    deleted = set()
    for item in overlay["patches"]:
        if "target" in item:
            continue
        patch = yaml.safe_load(item["patch"])
        if patch.get("$patch") == "delete":
            deleted.add((patch["kind"], patch["metadata"]["name"]))

    assert deleted == {
        ("Deployment", "edp-indexing"),
        ("Deployment", "edp-export"),
        ("PodDisruptionBudget", "edp-indexing"),
        ("PodDisruptionBudget", "edp-export"),
    }
