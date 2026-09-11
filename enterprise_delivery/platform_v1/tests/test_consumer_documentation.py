"""Keep published consumer examples aligned with the executable API contract."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from enterprise_data_platform.aggregation import AggregateRequest
from enterprise_data_platform.api import create_app
from enterprise_data_platform.control_state import ControlState
from enterprise_data_platform.models import (
    ErrorResponse,
    ExportJob,
    ExportRequest,
    Capability,
    LookupRequest,
    RetrievalContractResponse,
    RetrievalResponse,
    RetrieveRequest,
    SearchRequest,
    StructuredQueryRequest,
    StructuredQueryResponse,
    VectorSearchRequest,
)
from test_platform_core import allow_policy, build_service, principal


ROOT = Path(__file__).parents[1]
EXAMPLES = ROOT / "examples" / "consumers"
DOCS = ROOT / "docs" / "consumers"
PRODUCT_NAME = "SmartHub MCP & Agentic Gateway"


def load(path):
    return json.loads((EXAMPLES / path).read_text())


def test_published_request_and_response_examples_match_models():
    for path in [
        "regassist/lookup-request.json",
        "structured/lookup-request.json",
    ]:
        LookupRequest.model_validate(load(path))

    for path in [
        "regassist/q-mapping-query-request.json",
        "structured/query-request.json",
    ]:
        StructuredQueryRequest.model_validate(load(path))

    for path in [
        "regassist/lookup-response.json",
        "regassist/q-mapping-query-response.json",
        "structured/query-response.json",
        "structured/aggregate-response.json",
    ]:
        StructuredQueryResponse.model_validate(load(path))

    SearchRequest.model_validate(load("rag-quill/keyword-request.json"))
    for path in [
        "rag-quill/vector-query-text-request.json",
        "rag-quill/vector-direct-request.json",
    ]:
        value = VectorSearchRequest.model_validate(load(path))
        value.validate_one_input()

    RetrieveRequest.model_validate(load("rag-quill/retrieve-request.json"))
    for path in [
        "rag-quill/keyword-response.json",
        "rag-quill/vector-response.json",
        "rag-quill/retrieve-response.json",
    ]:
        RetrievalResponse.model_validate(load(path))

    AggregateRequest.model_validate(load("structured/aggregate-request.json"))
    ExportRequest.model_validate(load("structured/export-request.json"))
    ExportJob.model_validate(load("structured/export-response.json"))

    ErrorResponse.model_validate(load("common/error-response.json"))
    RetrievalContractResponse.model_validate(
        load("common/retrieval-contract-response.json")
    )


def test_retrieval_contract_is_caller_specific_and_machine_discoverable():
    service, catalog, policies = build_service()
    app = create_app(
        service=service,
        catalog=catalog,
        policies=policies,
        control_state=ControlState(),
        principal_resolver=lambda _: principal(),
        control_admin_check=lambda _: False,
    )
    client = TestClient(app)

    response = client.get("/v1/datasets/regulatory-docs/retrieval-contract")
    assert response.status_code == 200, response.text
    contract = response.json()
    assert set(contract["operations"]) == {"keyword", "vector", "hybrid", "retrieve"}
    assert contract["operations"]["vector"]["oauth_scope"] == "edp:vector"
    assert contract["operations"]["vector"]["max_top_k"] == 3
    assert contract["vector"] == {
        "profile_id": "medical-v1",
        "dimensions": 16,
        "distance": "cosine",
        "accepted_inputs": ["vector", "query_text"],
        "query_text_supported": True,
    }
    assert contract["scores"]["comparable_across_modes"] is False
    assert contract["result_identity"] == ["record_id", "chunk_id"]

    openapi = client.get("/openapi.json").json()
    assert openapi["info"]["title"] == f"{PRODUCT_NAME} API"
    assert "/v1/datasets/{dataset_id}/retrieval-contract" in openapi["paths"]
    operation = openapi["paths"]["/v1/datasets/{dataset_id}/retrieval-contract"]["get"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RetrievalContractResponse"
    )
    assert operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorResponse"
    )

    service.embedder = SimpleNamespace(models={})
    no_query_embedding = client.get(
        "/v1/datasets/regulatory-docs/retrieval-contract"
    ).json()
    assert no_query_embedding["vector"]["accepted_inputs"] == ["vector"]
    assert no_query_embedding["vector"]["query_text_supported"] is False

    policies.put(
        allow_policy().model_copy(update={"operations": {Capability.KEYWORD}})
    )
    reduced = client.get("/v1/datasets/regulatory-docs/retrieval-contract").json()
    assert set(reduced["operations"]) == {"keyword"}
    assert reduced["vector"] is None


def test_hybrid_response_reports_the_final_score_kind():
    service, _, _ = build_service()
    result = service.hybrid_search(
        principal(),
        "regulatory-docs",
        SearchRequest(query="adverse event", top_k=3, return_text=True),
    )
    assert result.results
    assert result.score_kind == "weighted_rrf"
    assert all("hybrid" in hit.scores for hit in result.results)


def test_consumer_document_links_resolve_inside_the_repository():
    documents = sorted(DOCS.glob("*.md"))
    assert {path.name for path in documents} == {
        "README.md",
        "COMMON_API_CONTRACT.md",
        "RAG_QUILL_INTEGRATION.md",
        "REGASSIST_INTEGRATION.md",
        "STRUCTURED_DATA_INTEGRATION.md",
    }
    for document in documents:
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", document.read_text()):
            if target.startswith(("http://", "https://", "#")):
                continue
            path = (document.parent / target.split("#", 1)[0]).resolve()
            assert path.exists(), f"{document.name} has broken link: {target}"
