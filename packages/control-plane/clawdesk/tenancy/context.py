"""
TenancyContext — the central context manager.

Manages the current tenant context and orchestrates the bootstrapper chain.
Inspired by stancl/tenancy's Tenancy class.

Key behaviors:
- initialize(tenant) → runs all bootstrappers in order
- end() → reverts all bootstrappers in reverse order
- run(tenant, fn) → atomic: initialize, run callback, revert (even on error)
- Only reverts bootstrappers that were actually initialized
- If a bootstrapper fails, reverts all previously initialized ones
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Protocol

from clawdesk.models import Tenant

logger = logging.getLogger(__name__)


class TenancyBootstrapper(Protocol):
    """Interface for bootstrappers in the chain."""

    @property
    def name(self) -> str: ...

    async def bootstrap(self, tenant: Tenant) -> None: ...

    async def revert(self) -> None: ...


class TenancyContext:
    def __init__(self, bootstrappers: list[TenancyBootstrapper]) -> None:
        self._bootstrappers = bootstrappers
        self._tenant: Tenant | None = None
        self._initialized = False
        self._initialized_bootstrappers: list[TenancyBootstrapper] = []

    @property
    def tenant(self) -> Tenant | None:
        return self._tenant

    @property
    def initialized(self) -> bool:
        return self._initialized

    async def initialize(self, tenant: Tenant) -> None:
        """Initialize tenancy for the given tenant."""
        # No-op if already initialized for the same tenant
        if self._initialized and self._tenant and self._tenant.id == tenant.id:
            return

        # End previous tenancy if initialized for a different tenant
        if self._initialized:
            await self.end()

        self._tenant = tenant
        self._initialized_bootstrappers = []

        # Run bootstrappers in order; revert on failure
        for bootstrapper in self._bootstrappers:
            try:
                await bootstrapper.bootstrap(tenant)
                self._initialized_bootstrappers.append(bootstrapper)
            except Exception:
                await self._revert_initialized()
                self._tenant = None
                self._initialized = False
                raise

        self._initialized = True

    async def end(self) -> None:
        """End tenancy, reverting to central context."""
        if not self._initialized:
            return

        await self._revert_initialized()

        self._tenant = None
        self._initialized = False
        self._initialized_bootstrappers = []

    async def run(
        self, tenant: Tenant, callback: Callable[[Tenant], Awaitable[Any]]
    ) -> Any:
        """Run a callback in a tenant's context. Atomic — always reverts."""
        previous_tenant = self._tenant

        try:
            await self.initialize(tenant)
            result = await callback(tenant)
        finally:
            if previous_tenant:
                await self.initialize(previous_tenant)
            else:
                await self.end()

        return result

    async def central(
        self, callback: Callable[[Tenant | None], Awaitable[Any]]
    ) -> Any:
        """Run a callback in central (non-tenant) context. Restores previous context."""
        previous_tenant = self._tenant

        await self.end()
        result = await callback(previous_tenant)

        if previous_tenant:
            await self.initialize(previous_tenant)

        return result

    async def run_for_multiple(
        self,
        tenants: list[Tenant],
        callback: Callable[[Tenant], Awaitable[None]],
    ) -> None:
        """Run a callback for each tenant. Restores original context."""
        original_tenant = self._tenant

        for tenant in tenants:
            await self.initialize(tenant)
            await callback(tenant)

        if original_tenant:
            await self.initialize(original_tenant)
        else:
            await self.end()

    async def _revert_initialized(self) -> None:
        """Revert initialized bootstrappers in reverse order."""
        for bootstrapper in reversed(self._initialized_bootstrappers):
            try:
                await bootstrapper.revert()
            except Exception:
                logger.exception(
                    'Failed to revert bootstrapper "%s"', bootstrapper.name
                )
