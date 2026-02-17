"""
TenantResolverChain — resolves tenant identity from request context.

Chain of responsibility: tries each resolver in order, first match wins.
"""

from __future__ import annotations

import re
from typing import Awaitable, Callable, Protocol

from clawdesk.models import RequestContext


class TenantResolver(Protocol):
    """Interface for tenant resolvers."""

    @property
    def name(self) -> str: ...

    async def resolve(self, context: RequestContext) -> str | None: ...


class TenantResolverChain:
    def __init__(self, resolvers: list[TenantResolver]) -> None:
        self._resolvers = resolvers

    async def resolve(self, context: RequestContext) -> str | None:
        """Try each resolver in order. First non-None result wins."""
        for resolver in self._resolvers:
            tenant_id = await resolver.resolve(context)
            if tenant_id is not None:
                return tenant_id
        return None

    @property
    def resolver_names(self) -> list[str]:
        return [r.name for r in self._resolvers]


# ─── Built-in Resolvers ──────────────────────────────────


LookupFn = Callable[[str], Awaitable[str | None]]

_IP_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


class HeaderTenantResolver:
    """Resolves tenant from a request header (e.g., X-Tenant-ID or X-API-Key)."""

    def __init__(self, header_name: str, lookup_fn: LookupFn) -> None:
        self._header_name = header_name.lower()
        self._lookup_fn = lookup_fn

    @property
    def name(self) -> str:
        return "header"

    async def resolve(self, context: RequestContext) -> str | None:
        value = context.headers.get(self._header_name)
        if not value:
            return None
        return await self._lookup_fn(value)


class SubdomainTenantResolver:
    """Resolves tenant from subdomain (e.g., acme.clawdesk.com → 'acme')."""

    def __init__(self, root_domain: str, lookup_fn: LookupFn) -> None:
        self._root_domain = root_domain.lower()
        self._lookup_fn = lookup_fn

    @property
    def name(self) -> str:
        return "subdomain"

    async def resolve(self, context: RequestContext) -> str | None:
        hostname = (context.hostname or "").lower()
        if not hostname:
            return None

        # Skip root domain itself
        if hostname == self._root_domain or hostname == f"www.{self._root_domain}":
            return None

        # Skip IP addresses
        if _IP_RE.match(hostname):
            return None

        # Extract subdomain
        suffix = f".{self._root_domain}"
        if not hostname.endswith(suffix):
            return None

        subdomain = hostname[: -len(suffix)]
        if not subdomain or "." in subdomain:
            return None  # Skip multi-level subdomains

        return await self._lookup_fn(subdomain)


class JwtClaimTenantResolver:
    """Resolves tenant from JWT claims (e.g., tenant_id or org_id claim)."""

    def __init__(self, claim_name: str = "tenant_id") -> None:
        self._claim_name = claim_name

    @property
    def name(self) -> str:
        return "jwt-claim"

    async def resolve(self, context: RequestContext) -> str | None:
        value = context.claims.get(self._claim_name)
        if isinstance(value, str) and value:
            return value
        return None
