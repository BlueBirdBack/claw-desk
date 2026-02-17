"""Shared test helpers."""

from __future__ import annotations

from clawdesk.models import (
    ModelRoutingConfig,
    Tenant,
    TenantBilling,
    TenantConfig,
    TenantStatus,
    UsageMetrics,
)


def make_tenant(**overrides) -> Tenant:
    """Create a test tenant with sensible defaults."""
    defaults = dict(
        id="tenant-1",
        name="Acme Corp",
        slug="acme",
        status=TenantStatus.ACTIVE,
        openclaw_agent_id="tenant-acme",
        config=TenantConfig(
            model_routing=ModelRoutingConfig(
                primary="azure/gpt-4o",
                escalation_sentiment=-0.5,
            ),
            confidence_threshold=0.7,
        ),
        billing=TenantBilling(
            plan="pro",
            usage_this_month=UsageMetrics(),
        ),
    )
    defaults.update(overrides)
    return Tenant(**defaults)
