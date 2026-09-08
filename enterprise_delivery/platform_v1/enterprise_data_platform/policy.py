from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any, Dict, Iterable, List, Optional, Set

from .models import (
    AccessPolicy,
    Capability,
    DataProduct,
    FieldPolicy,
    PolicyDecision,
    PolicyEffect,
    Principal,
)


def and_filters(*filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    parts = [f for f in filters if f]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    flattened: List[Dict[str, Any]] = []
    for item in parts:
        if set(item.keys()) == {"and"} and isinstance(item["and"], list):
            flattened.extend(item["and"])
        else:
            flattened.append(item)
    return {"and": flattened}


def or_filters(*filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    parts = [f for f in filters if f]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    flattened: List[Dict[str, Any]] = []
    for item in parts:
        if set(item.keys()) == {"or"} and isinstance(item["or"], list):
            flattened.extend(item["or"])
        else:
            flattened.append(item)
    return {"or": flattened}


def _principal_matches(policy: AccessPolicy, principal: Principal) -> bool:
    if policy.subjects and principal.subject not in policy.subjects:
        return False
    if policy.client_ids and (not principal.client_id or principal.client_id not in policy.client_ids):
        return False
    if policy.groups and not (policy.groups & principal.groups):
        return False
    if policy.tenants and (not principal.tenant or principal.tenant not in policy.tenants):
        return False
    return True


def _resource_matches(policy: AccessPolicy, dataset_id: str, operation: Capability) -> bool:
    if policy.operations and operation not in policy.operations:
        return False
    return any(fnmatchcase(dataset_id, pattern) for pattern in policy.dataset_patterns)


class PolicyEngine:
    """Small deterministic policy decision point for the v1 control plane.

    The production deployment can replace this implementation with OPA/Cedar or
    an enterprise PDP. The service contract stays the same: every data-plane
    operation receives a PolicyDecision before any source/index is touched.
    """

    def __init__(self, policies: Iterable[AccessPolicy] = ()) -> None:
        self._policies: Dict[str, AccessPolicy] = {p.id: p for p in policies}

    def put(self, policy: AccessPolicy) -> None:
        self._policies[policy.id] = policy

    def delete(self, policy_id: str) -> None:
        self._policies.pop(policy_id, None)

    def list(self) -> List[AccessPolicy]:
        return sorted(self._policies.values(), key=lambda p: (p.priority, p.id))

    def evaluate(
        self,
        *,
        principal: Principal,
        product: DataProduct,
        operation: Capability,
    ) -> PolicyDecision:
        if product.status.value != "active":
            return PolicyDecision(allowed=False, reason=f"dataset status is {product.status.value}")
        if operation not in product.capabilities:
            return PolicyDecision(allowed=False, reason=f"capability '{operation.value}' is not enabled")

        matching = [
            p
            for p in self.list()
            if p.enabled
            and _resource_matches(p, product.id, operation)
            and _principal_matches(p, principal)
        ]
        matching.sort(key=lambda p: (p.priority, p.id))

        denies = [p for p in matching if p.effect == PolicyEffect.DENY]
        if denies:
            return PolicyDecision(
                allowed=False,
                reason="explicit deny",
                matched_policy_ids=[p.id for p in denies],
            )

        allows = [p for p in matching if p.effect == PolicyEffect.ALLOW]
        if not allows:
            return PolicyDecision(allowed=False, reason="no matching allow policy")

        field_map = product.field_map()
        selectable = {name for name, f in field_map.items() if f.selectable and f.default_policy != FieldPolicy.HIDDEN}
        default_masked = {name for name, f in field_map.items() if f.default_policy == FieldPolicy.MASKED}

        granted_fields: Set[str] = set()
        denied_fields: Set[str] = set()
        row_grants: List[Dict[str, Any]] = []
        tenant_constraints: List[Dict[str, Any]] = []
        limits: List[int] = []
        top_ks: List[int] = []

        # A tenant-scoped product never becomes global because a policy author
        # forgot to repeat require_tenant_isolation on one grant.
        if product.tenant_field:
            if not principal.tenant:
                return PolicyDecision(allowed=False, reason="tenant identity is required")
            tenant_constraints.append({"field": product.tenant_field, "op": "eq", "value": principal.tenant})

        for policy in allows:
            granted_fields |= selectable if policy.allowed_fields is None else (selectable & policy.allowed_fields)
            denied_fields |= policy.denied_fields
            if policy.mandatory_filter:
                row_grants.append(policy.mandatory_filter)
            if policy.max_limit is not None:
                limits.append(policy.max_limit)
            if policy.max_top_k is not None:
                top_ks.append(policy.max_top_k)
            if policy.require_tenant_isolation:
                if not product.tenant_field:
                    return PolicyDecision(
                        allowed=False,
                        reason=f"policy '{policy.id}' requires tenant isolation but dataset has no tenant_field",
                        matched_policy_ids=[p.id for p in allows],
                    )
                if not principal.tenant:
                    return PolicyDecision(
                        allowed=False,
                        reason="tenant identity is required",
                        matched_policy_ids=[p.id for p in allows],
                    )
                tenant_constraints.append(
                    {"field": product.tenant_field, "op": "eq", "value": principal.tenant}
                )

        # A single row predicate cannot express field grants conditional on rows.
        # Intersect fields for heterogeneous row scopes to avoid a field/row
        # cross-product privilege escalation. A future per-cell PDP may widen it.
        import json
        scopes = {json.dumps(p.mandatory_filter, sort_keys=True) for p in allows}
        if len(scopes) > 1:
            for policy in allows:
                granted_fields &= selectable if policy.allowed_fields is None else policy.allowed_fields
        granted_fields -= denied_fields
        if not granted_fields:
            return PolicyDecision(
                allowed=False,
                reason="matching policies grant no selectable fields",
                matched_policy_ids=[p.id for p in allows],
            )

        # Multiple allow grants are additive, so their row scopes are ORed. Hard
        # tenant isolation constraints remain ANDed with that grant scope.
        grant_filter = None if any(not p.mandatory_filter for p in allows) else or_filters(*row_grants)
        tenant_filter = and_filters(*[dict(t) for i, t in enumerate(tenant_constraints) if t not in tenant_constraints[:i]])
        mandatory_filter = and_filters(grant_filter, tenant_filter)

        return PolicyDecision(
            allowed=True,
            reason="allowed",
            matched_policy_ids=[p.id for p in allows],
            allowed_fields=granted_fields,
            masked_fields=default_masked & granted_fields,
            mandatory_filter=mandatory_filter,
            max_limit=min(product.max_limit, max(limits) if limits else product.max_limit),
            max_top_k=min(product.max_top_k, max(top_ks) if top_ks else product.max_top_k),
        )
