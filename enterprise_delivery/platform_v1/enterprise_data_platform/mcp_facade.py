from __future__ import annotations

from typing import Any, Dict, List

from .catalog import CatalogStore
from .control_state import ControlState
from .models import Capability, Principal, RetrieveRequest, SearchRequest, StructuredQueryRequest
from .services import AccessDenied, PlatformService


class MCPFacade:
    """Policy-preserving MCP facade over the same retrieval core.

    This module intentionally contains no database/index access. MCP is only a
    transport/tool surface. Agent registration, dataset permissions and the
    normal platform policy decision point are all evaluated before execution.
    """

    def __init__(self, *, service: PlatformService, catalog: CatalogStore, control: ControlState) -> None:
        self.service = service
        self.catalog = catalog
        self.control = control

    @staticmethod
    def tool_definitions() -> List[Dict[str, Any]]:
        return [
            {
                "name": "describe_dataset",
                "description": "Describe an approved data product and its capabilities.",
                "inputSchema": {"type": "object", "required": ["dataset_id"], "properties": {"dataset_id": {"type": "string"}}},
            },
            {
                "name": "query_dataset",
                "description": "Run a governed structured query using cursor pagination.",
                "inputSchema": {"type": "object", "required": ["dataset_id", "request"], "properties": {"dataset_id": {"type": "string"}, "request": {"type": "object"}}},
            },
            {
                "name": "search_dataset",
                "description": "Run approved keyword or hybrid retrieval.",
                "inputSchema": {"type": "object", "required": ["dataset_id", "query"], "properties": {"dataset_id": {"type": "string"}, "query": {"type": "string"}, "mode": {"enum": ["keyword", "hybrid"]}, "top_k": {"type": "integer"}}},
            },
            {
                "name": "retrieve_context",
                "description": "Return governed RAG-ready clear text chunks with source metadata.",
                "inputSchema": {"type": "object", "required": ["dataset_id", "query"], "properties": {"dataset_id": {"type": "string"}, "query": {"type": "string"}, "mode": {"enum": ["keyword", "vector", "hybrid"]}, "top_k": {"type": "integer"}}},
            },
        ]

    def invoke(self, *, agent_id: str, principal: Principal, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        agent = self.control.agents.get(agent_id)
        if not agent.mcp_enabled or agent.status.value != "active":
            raise AccessDenied("MCP is disabled for this agent")
        if principal.subject != agent.service_principal:
            raise AccessDenied("principal does not match the registered agent service principal")

        dataset_id = str(arguments.get("dataset_id") or "")
        if not dataset_id or dataset_id not in agent.allowed_datasets:
            raise AccessDenied("dataset is not approved for this agent")

        if tool == "describe_dataset":
            product = self.catalog.get(dataset_id)
            return {
                "id": product.id,
                "display_name": product.display_name,
                "version": product.version,
                "capabilities": sorted(c.value for c in product.capabilities if c in agent.allowed_capabilities or c == Capability.DISCOVER),
                "identity_fields": product.identity_fields,
            }

        if tool == "query_dataset":
            self._require_agent_capability(agent.allowed_capabilities, Capability.QUERY)
            result = self.service.query(principal, dataset_id, StructuredQueryRequest.model_validate(arguments.get("request") or {}))
            return result.model_dump(mode="json")

        if tool == "search_dataset":
            mode = arguments.get("mode") or "hybrid"
            top_k = min(int(arguments.get("top_k") or agent.max_top_k), agent.max_top_k)
            request = SearchRequest(query=str(arguments.get("query") or ""), filter=arguments.get("filter"), top_k=top_k, return_text=False)
            if mode == "keyword":
                self._require_agent_capability(agent.allowed_capabilities, Capability.KEYWORD)
                result = self.service.keyword_search(principal, dataset_id, request)
            else:
                self._require_agent_capability(agent.allowed_capabilities, Capability.HYBRID)
                result = self.service.hybrid_search(principal, dataset_id, request)
            return result.model_dump(mode="json")

        if tool == "retrieve_context":
            self._require_agent_capability(agent.allowed_capabilities, Capability.RETRIEVE)
            top_k = min(int(arguments.get("top_k") or agent.max_top_k), agent.max_top_k)
            request = RetrieveRequest(
                query=str(arguments.get("query") or ""),
                mode=arguments.get("mode") or "hybrid",
                filter=arguments.get("filter"),
                top_k=top_k,
            )
            result = self.service.retrieve(principal, dataset_id, request)
            if agent.require_citations:
                result.results = [hit for hit in result.results if hit.source.get("record_id") and hit.source.get("dataset")]
            total_chars = 0
            bounded = []
            for hit in result.results:
                size = len(hit.text or "")
                if total_chars + size > agent.max_context_chars:
                    break
                bounded.append(hit)
                total_chars += size
            result.results = bounded
            return result.model_dump(mode="json")

        raise ValueError(f"unknown MCP tool '{tool}'")

    @staticmethod
    def _require_agent_capability(capabilities, required: Capability) -> None:
        if required not in capabilities:
            raise AccessDenied(f"agent is not approved for capability '{required.value}'")
