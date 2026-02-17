"""Tests for TenancyContext — bootstrapper chain with init/revert/run semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from clawdesk.models import Tenant
from clawdesk.tenancy.context import TenancyContext
from tests.helpers import make_tenant


# ─── Helpers ──────────────────────────────────────────────


@dataclass
class FakeBootstrapper:
    _name: str
    log: list[str]
    fail_on_bootstrap: bool = False

    @property
    def name(self) -> str:
        return self._name

    async def bootstrap(self, tenant: Tenant) -> None:
        self.log.append(f"{self._name}:up")
        if self.fail_on_bootstrap:
            raise RuntimeError(f"{self._name} failed")

    async def revert(self) -> None:
        self.log.append(f"{self._name}:down")


def make_bootstrapper(name: str, log: list[str]) -> FakeBootstrapper:
    return FakeBootstrapper(_name=name, log=log)


def make_failing_bootstrapper(name: str, log: list[str]) -> FakeBootstrapper:
    return FakeBootstrapper(_name=name, log=log, fail_on_bootstrap=True)


# ─── initialize ──────────────────────────────────────────


class TestInitialize:
    async def test_runs_all_bootstrappers_in_order(self):
        log: list[str] = []
        ctx = TenancyContext([
            make_bootstrapper("A", log),
            make_bootstrapper("B", log),
            make_bootstrapper("C", log),
        ])

        await ctx.initialize(make_tenant())

        assert log == ["A:up", "B:up", "C:up"]
        assert ctx.initialized is True
        assert ctx.tenant is not None
        assert ctx.tenant.id == "tenant-1"

    async def test_noop_if_already_initialized_for_same_tenant(self):
        log: list[str] = []
        ctx = TenancyContext([make_bootstrapper("A", log)])
        tenant = make_tenant()

        await ctx.initialize(tenant)
        await ctx.initialize(tenant)

        assert log == ["A:up"]  # Only once

    async def test_ends_previous_tenancy_before_initializing_new(self):
        log: list[str] = []
        ctx = TenancyContext([make_bootstrapper("A", log)])

        await ctx.initialize(make_tenant(id="tenant-1"))
        await ctx.initialize(make_tenant(id="tenant-2"))

        assert log == ["A:up", "A:down", "A:up"]
        assert ctx.tenant is not None
        assert ctx.tenant.id == "tenant-2"


# ─── end ─────────────────────────────────────────────────


class TestEnd:
    async def test_reverts_bootstrappers_in_reverse_order(self):
        log: list[str] = []
        ctx = TenancyContext([
            make_bootstrapper("A", log),
            make_bootstrapper("B", log),
            make_bootstrapper("C", log),
        ])

        await ctx.initialize(make_tenant())
        log.clear()

        await ctx.end()

        assert log == ["C:down", "B:down", "A:down"]
        assert ctx.initialized is False
        assert ctx.tenant is None

    async def test_noop_if_not_initialized(self):
        log: list[str] = []
        ctx = TenancyContext([make_bootstrapper("A", log)])

        await ctx.end()

        assert log == []


# ─── error handling ──────────────────────────────────────


class TestErrorHandling:
    async def test_reverts_previously_initialized_if_one_fails(self):
        log: list[str] = []
        ctx = TenancyContext([
            make_bootstrapper("A", log),
            make_bootstrapper("B", log),
            make_failing_bootstrapper("C", log),
            make_bootstrapper("D", log),  # Should never run
        ])

        with pytest.raises(RuntimeError, match="C failed"):
            await ctx.initialize(make_tenant())

        assert log == ["A:up", "B:up", "C:up", "B:down", "A:down"]
        assert ctx.initialized is False
        assert ctx.tenant is None

    async def test_does_not_revert_uninitialized_bootstrappers(self):
        log: list[str] = []
        d = make_bootstrapper("D", log)
        d_revert = AsyncMock()
        d.revert = d_revert  # type: ignore

        ctx = TenancyContext([
            make_bootstrapper("A", log),
            make_failing_bootstrapper("B", log),
            make_bootstrapper("C", log),  # Never reached
            d,  # Never reached
        ])

        with pytest.raises(RuntimeError, match="B failed"):
            await ctx.initialize(make_tenant())

        d_revert.assert_not_called()


# ─── run ─────────────────────────────────────────────────


class TestRun:
    async def test_initializes_runs_callback_then_reverts(self):
        log: list[str] = []
        ctx = TenancyContext([make_bootstrapper("A", log)])
        tenant = make_tenant()

        result = await ctx.run(tenant, self._callback_returning_name(log))

        assert result == "Acme Corp"
        assert log == ["A:up", "callback", "A:down"]
        assert ctx.initialized is False

    async def test_reverts_even_if_callback_throws(self):
        log: list[str] = []
        ctx = TenancyContext([make_bootstrapper("A", log)])

        with pytest.raises(RuntimeError, match="callback boom"):
            await ctx.run(make_tenant(), self._callback_that_throws(log))

        assert log == ["A:up", "callback", "A:down"]
        assert ctx.initialized is False

    async def test_restores_previous_tenant_context(self):
        log: list[str] = []
        ctx = TenancyContext([make_bootstrapper("A", log)])

        tenant_a = make_tenant(id="tenant-a", name="Tenant A")
        tenant_b = make_tenant(id="tenant-b", name="Tenant B")

        await ctx.initialize(tenant_a)
        log.clear()

        async def check_tenant(t: Tenant):
            assert ctx.tenant is not None
            assert ctx.tenant.id == "tenant-b"

        await ctx.run(tenant_b, check_tenant)

        assert ctx.tenant is not None
        assert ctx.tenant.id == "tenant-a"
        assert ctx.initialized is True

    @staticmethod
    def _callback_returning_name(log: list[str]):
        async def callback(t: Tenant):
            log.append("callback")
            return t.name
        return callback

    @staticmethod
    def _callback_that_throws(log: list[str]):
        async def callback(t: Tenant):
            log.append("callback")
            raise RuntimeError("callback boom")
        return callback


# ─── central ─────────────────────────────────────────────


class TestCentral:
    async def test_reverts_to_central_then_restores(self):
        log: list[str] = []
        ctx = TenancyContext([make_bootstrapper("A", log)])
        tenant = make_tenant()

        await ctx.initialize(tenant)
        log.clear()

        async def check_central(prev: Tenant | None):
            assert prev is not None
            assert prev.id == "tenant-1"
            assert ctx.initialized is False
            log.append("central-work")

        await ctx.central(check_central)

        assert ctx.tenant is not None
        assert ctx.tenant.id == "tenant-1"
        assert log == ["A:down", "central-work", "A:up"]


# ─── run_for_multiple ────────────────────────────────────


class TestRunForMultiple:
    async def test_runs_callback_for_each_tenant_and_restores(self):
        log: list[str] = []
        ctx = TenancyContext([make_bootstrapper("A", log)])
        tenants = [
            make_tenant(id="t1", name="T1"),
            make_tenant(id="t2", name="T2"),
            make_tenant(id="t3", name="T3"),
        ]

        visited: list[str] = []

        async def visit(t: Tenant) -> None:
            visited.append(t.id)

        await ctx.run_for_multiple(tenants, visit)

        assert visited == ["t1", "t2", "t3"]
        assert ctx.initialized is False
        assert ctx.tenant is None
