import pytest

from enterprise_data_platform.control_models import GuardrailRule
from enterprise_data_platform.guardrails import GuardrailEngine, GuardrailViolation
from enterprise_data_platform.models import Capability
from test_platform_core import product, principal


def test_guardrail_caps_top_k_and_requires_citations():
    rules = [
        GuardrailRule(id="cap-top-k", name="Cap top-k", kind="max_top_k", action="limit", config={"max": 7}),
        GuardrailRule(id="citations", name="Citations", kind="require_citations", action="deny"),
    ]
    engine = GuardrailEngine(lambda: rules)
    d = engine.evaluate(principal=principal(), product=product(), operation=Capability.RETRIEVE, top_k=100)
    assert d.max_top_k == 7
    assert d.require_citations is True


def test_sensitive_export_can_be_blocked_globally():
    engine = GuardrailEngine(
        lambda: [GuardrailRule(id="no-secrets", name="No sensitive export", kind="deny_sensitive_export", action="deny")]
    )
    with pytest.raises(GuardrailViolation, match="sensitive fields"):
        engine.evaluate(
            principal=principal(),
            product=product(),
            operation=Capability.EXPORT,
            requested_fields=["id", "secret"],
        )
