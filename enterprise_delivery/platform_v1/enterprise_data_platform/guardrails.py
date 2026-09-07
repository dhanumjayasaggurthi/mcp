from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Sequence, Set

from .control_models import GuardrailAction, GuardrailRule
from .models import Capability, DataProduct, Principal


class GuardrailViolation(PermissionError):
    pass


@dataclass
class GuardrailDecision:
    allowed: bool = True
    reason: str = "allowed"
    matched_rule_ids: List[str] = field(default_factory=list)
    max_top_k: Optional[int] = None
    max_limit: Optional[int] = None
    require_citations: bool = False
    redact_pii: bool = False
    audit: bool = False


class GuardrailEngine:
    """Runtime guardrail enforcement fed by Control Hub rules.

    Policy decides *who may access what*. Guardrails impose operational and AI
    safety constraints on an otherwise-authorized request. They are intentionally
    additive and cannot grant access denied by policy.
    """

    def __init__(self, rule_provider: Callable[[], Iterable[GuardrailRule]]) -> None:
        self._rule_provider = rule_provider

    def evaluate(
        self,
        *,
        principal: Principal,
        product: DataProduct,
        operation: Capability,
        requested_fields: Sequence[str] = (),
        top_k: Optional[int] = None,
        limit: Optional[int] = None,
        signals: Set[str] | None = None,
    ) -> GuardrailDecision:
        decision = GuardrailDecision()
        signals = signals or set()
        rules = sorted((r for r in self._rule_provider() if r.enabled), key=lambda r: r.id)
        sensitive = {f.name for f in product.fields if f.sensitive}

        for rule in rules:
            if not self._scope_matches(rule, principal, product):
                continue
            matched = False
            if rule.kind == "deny_sensitive_export":
                matched = operation == Capability.EXPORT and bool(sensitive & set(requested_fields))
                if matched and rule.action == GuardrailAction.DENY:
                    raise GuardrailViolation(f"guardrail '{rule.id}' blocks export of sensitive fields")
            elif rule.kind == "max_top_k" and top_k is not None:
                configured = int(rule.config.get("max", top_k))
                if top_k > configured:
                    matched = True
                    decision.max_top_k = configured if decision.max_top_k is None else min(decision.max_top_k, configured)
            elif rule.kind == "max_scan_budget" and limit is not None:
                configured = int(rule.config.get("max_rows", limit))
                if limit > configured:
                    matched = True
                    decision.max_limit = configured if decision.max_limit is None else min(decision.max_limit, configured)
            elif rule.kind == "require_citations":
                if operation in {Capability.RETRIEVE, Capability.MCP}:
                    matched = True
                    decision.require_citations = True
            elif rule.kind == "pii_redaction":
                if operation in {Capability.QUERY, Capability.KEYWORD, Capability.VECTOR, Capability.HYBRID, Capability.RETRIEVE}:
                    matched = True
                    decision.redact_pii = True
            elif rule.kind == "prompt_injection_signal":
                blocked = set(rule.config.get("signals", ["prompt_injection_high_confidence"]))
                matched = bool(signals & blocked)
                if matched and rule.action == GuardrailAction.DENY:
                    raise GuardrailViolation(f"guardrail '{rule.id}' blocked a classified prompt-injection signal")
            elif rule.kind in {"rate_limit", "schema_drift", "index_freshness", "content_classification"}:
                # These rules are enforced by the gateway/indexing/monitoring
                # components. Mark for audit here when context reaches the data plane.
                matched = False

            if matched:
                decision.matched_rule_ids.append(rule.id)
                if rule.action == GuardrailAction.AUDIT:
                    decision.audit = True

        return decision

    @staticmethod
    def _scope_matches(rule: GuardrailRule, principal: Principal, product: DataProduct) -> bool:
        if rule.scope == "global":
            return True
        if rule.scope == "dataset":
            return rule.target == product.id
        if rule.scope == "client":
            return bool(principal.client_id and rule.target == principal.client_id)
        # Agent-scoped guardrails are applied by the MCP/agent facade where an
        # agent registration ID exists. Standard REST identity has no agent ID.
        return False
