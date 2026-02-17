"""Tests for TenantResolverChain and built-in resolvers."""

from __future__ import annotations

import pytest

from clawdesk.models import RequestContext
from clawdesk.tenancy.resolver import (
    HeaderTenantResolver,
    JwtClaimTenantResolver,
    SubdomainTenantResolver,
    TenantResolverChain,
)


# ─── Helpers ──────────────────────────────────────────────


async def lookup_direct(value: str) -> str | None:
    """Simple lookup that returns the value as-is."""
    return value


async def lookup_map(value: str) -> str | None:
    """Lookup from a fixed map."""
    db = {"acme": "tenant-acme", "globex": "tenant-globex", "key-123": "tenant-123"}
    return db.get(value)


async def lookup_none(_: str) -> str | None:
    """Always returns None."""
    return None


# ─── TenantResolverChain ─────────────────────────────────


class TestResolverChain:
    async def test_returns_first_match(self):
        chain = TenantResolverChain([
            HeaderTenantResolver("x-tenant-id", lookup_direct),
        ])

        result = await chain.resolve(
            RequestContext(headers={"x-tenant-id": "tenant-1"})
        )
        assert result == "tenant-1"

    async def test_returns_none_when_no_resolvers_match(self):
        chain = TenantResolverChain([
            HeaderTenantResolver("x-tenant-id", lookup_none),
        ])

        result = await chain.resolve(RequestContext(headers={"x-tenant-id": "anything"}))
        assert result is None

    async def test_tries_resolvers_in_order(self):
        chain = TenantResolverChain([
            HeaderTenantResolver("x-api-key", lookup_none),  # Won't match
            HeaderTenantResolver("x-tenant-id", lookup_direct),  # Will match
        ])

        result = await chain.resolve(
            RequestContext(headers={"x-tenant-id": "tenant-2", "x-api-key": "bad"})
        )
        assert result == "tenant-2"

    async def test_stops_at_first_match(self):
        chain = TenantResolverChain([
            HeaderTenantResolver("x-tenant-id", lookup_direct),
            HeaderTenantResolver("x-api-key", lookup_direct),  # Should not be called
        ])

        result = await chain.resolve(
            RequestContext(headers={"x-tenant-id": "first", "x-api-key": "second"})
        )
        assert result == "first"

    async def test_returns_none_with_empty_chain(self):
        chain = TenantResolverChain([])
        result = await chain.resolve(RequestContext())
        assert result is None

    async def test_resolver_names(self):
        chain = TenantResolverChain([
            HeaderTenantResolver("x-tenant-id", lookup_direct),
            SubdomainTenantResolver("clawdesk.com", lookup_direct),
            JwtClaimTenantResolver("tenant_id"),
        ])
        assert chain.resolver_names == ["header", "subdomain", "jwt-claim"]


# ─── HeaderTenantResolver ────────────────────────────────


class TestHeaderResolver:
    async def test_resolves_from_header(self):
        resolver = HeaderTenantResolver("x-api-key", lookup_map)

        result = await resolver.resolve(
            RequestContext(headers={"x-api-key": "key-123"})
        )
        assert result == "tenant-123"

    async def test_returns_none_when_header_missing(self):
        resolver = HeaderTenantResolver("x-api-key", lookup_map)

        result = await resolver.resolve(RequestContext(headers={}))
        assert result is None

    async def test_returns_none_when_lookup_fails(self):
        resolver = HeaderTenantResolver("x-api-key", lookup_map)

        result = await resolver.resolve(
            RequestContext(headers={"x-api-key": "unknown-key"})
        )
        assert result is None

    async def test_header_name_is_case_insensitive(self):
        resolver = HeaderTenantResolver("X-API-Key", lookup_map)

        result = await resolver.resolve(
            RequestContext(headers={"x-api-key": "key-123"})
        )
        assert result == "tenant-123"


# ─── SubdomainTenantResolver ─────────────────────────────


class TestSubdomainResolver:
    async def test_resolves_from_subdomain(self):
        resolver = SubdomainTenantResolver("clawdesk.com", lookup_map)

        result = await resolver.resolve(
            RequestContext(hostname="acme.clawdesk.com")
        )
        assert result == "tenant-acme"

    async def test_returns_none_for_root_domain(self):
        resolver = SubdomainTenantResolver("clawdesk.com", lookup_direct)

        result = await resolver.resolve(
            RequestContext(hostname="clawdesk.com")
        )
        assert result is None

    async def test_returns_none_for_www(self):
        resolver = SubdomainTenantResolver("clawdesk.com", lookup_direct)

        result = await resolver.resolve(
            RequestContext(hostname="www.clawdesk.com")
        )
        assert result is None

    async def test_returns_none_for_ip_address(self):
        resolver = SubdomainTenantResolver("clawdesk.com", lookup_direct)

        result = await resolver.resolve(
            RequestContext(hostname="192.168.1.1")
        )
        assert result is None

    async def test_returns_none_for_different_domain(self):
        resolver = SubdomainTenantResolver("clawdesk.com", lookup_direct)

        result = await resolver.resolve(
            RequestContext(hostname="acme.other.com")
        )
        assert result is None

    async def test_returns_none_for_multi_level_subdomain(self):
        resolver = SubdomainTenantResolver("clawdesk.com", lookup_direct)

        result = await resolver.resolve(
            RequestContext(hostname="a.b.clawdesk.com")
        )
        assert result is None

    async def test_returns_none_when_hostname_missing(self):
        resolver = SubdomainTenantResolver("clawdesk.com", lookup_direct)

        result = await resolver.resolve(RequestContext())
        assert result is None

    async def test_returns_none_when_lookup_fails(self):
        resolver = SubdomainTenantResolver("clawdesk.com", lookup_map)

        result = await resolver.resolve(
            RequestContext(hostname="unknown.clawdesk.com")
        )
        assert result is None


# ─── JwtClaimTenantResolver ──────────────────────────────


class TestJwtClaimResolver:
    async def test_resolves_from_default_claim(self):
        resolver = JwtClaimTenantResolver()

        result = await resolver.resolve(
            RequestContext(claims={"tenant_id": "tenant-1"})
        )
        assert result == "tenant-1"

    async def test_resolves_from_custom_claim(self):
        resolver = JwtClaimTenantResolver("org_id")

        result = await resolver.resolve(
            RequestContext(claims={"org_id": "org-42"})
        )
        assert result == "org-42"

    async def test_returns_none_when_claim_missing(self):
        resolver = JwtClaimTenantResolver()

        result = await resolver.resolve(RequestContext(claims={}))
        assert result is None

    async def test_returns_none_when_claim_is_not_string(self):
        resolver = JwtClaimTenantResolver()

        result = await resolver.resolve(
            RequestContext(claims={"tenant_id": 123})
        )
        assert result is None

    async def test_returns_none_when_claim_is_empty_string(self):
        resolver = JwtClaimTenantResolver()

        result = await resolver.resolve(
            RequestContext(claims={"tenant_id": ""})
        )
        assert result is None
