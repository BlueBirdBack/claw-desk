"""Tests for TenantProvisioner — maps tenants to OpenClaw agents."""

from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from clawdesk.models import (
    ModelRoutingConfig,
    OpenClawAgentConfig,
    OpenClawConfigSnapshot,
    TenantConfig,
)
from clawdesk.openclaw.provisioner import ProvisionerOptions, TenantProvisioner
from tests.helpers import make_tenant


# ─── Mock Gateway ─────────────────────────────────────────


@dataclass
class MockGateway:
    """Mock OpenClaw gateway for testing provisioner."""

    _config: dict[str, Any] = field(default_factory=lambda: {"agents": {"list": []}})
    _hash: str = "hash-1"
    patch_config_calls: list[tuple[dict, str]] = field(default_factory=list)
    _fail_patch: bool = False

    async def get_config(self) -> OpenClawConfigSnapshot:
        return OpenClawConfigSnapshot(config=deepcopy(self._config), hash=self._hash)

    async def patch_config(self, patch: dict, base_hash: str) -> None:
        if self._fail_patch:
            raise RuntimeError("config write failed")
        if base_hash != self._hash:
            raise ValueError("Config hash mismatch")

        self.patch_config_calls.append((patch, base_hash))

        # Apply the patch
        if "agents" in patch:
            self._config["agents"] = patch["agents"]
        self._hash = f"hash-{len(self.patch_config_calls)}"

    @property
    def agent_list(self) -> list[dict]:
        return self._config.get("agents", {}).get("list", [])


# ─── Tests ────────────────────────────────────────────────


class TestTenantToAgentId:
    def test_prefixes_slug_with_tenant(self):
        gw = MockGateway()
        p = TenantProvisioner(ProvisionerOptions(workspace_base_dir="/tmp", gateway=gw))

        assert p.tenant_to_agent_id("acme") == "tenant-acme"
        assert p.tenant_to_agent_id("my-corp") == "tenant-my-corp"


class TestBuildAgentConfig:
    def test_builds_correct_config(self):
        gw = MockGateway()
        p = TenantProvisioner(ProvisionerOptions(workspace_base_dir="/tmp", gateway=gw))
        tenant = make_tenant()

        config = p.build_agent_config(tenant, "/data/tenants/tenant-acme")

        assert config.id == "tenant-acme"
        assert config.name == "Acme Corp"
        assert config.workspace == "/data/tenants/tenant-acme"
        assert config.model == "azure/gpt-4o"
        assert config.sandbox == {"mode": "all", "workspaceAccess": "rw"}

    def test_includes_fallbacks_when_specified(self):
        gw = MockGateway()
        p = TenantProvisioner(ProvisionerOptions(workspace_base_dir="/tmp", gateway=gw))
        tenant = make_tenant(
            config=TenantConfig(
                model_routing=ModelRoutingConfig(
                    primary="azure/gpt-4o",
                    fallbacks=["azure/gpt-4o-mini"],
                    escalation_sentiment=-0.5,
                ),
                confidence_threshold=0.7,
            )
        )

        config = p.build_agent_config(tenant, "/data/tenants/tenant-acme")

        assert config.model == {
            "primary": "azure/gpt-4o",
            "fallbacks": ["azure/gpt-4o-mini"],
        }


class TestProvision:
    async def test_creates_workspace_and_patches_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = MockGateway()
            p = TenantProvisioner(ProvisionerOptions(workspace_base_dir=tmpdir, gateway=gw))
            tenant = make_tenant()

            result = await p.provision(tenant)

            assert result.status == "success"
            assert result.agent_id == "tenant-acme"

            # Workspace files created
            workspace = Path(tmpdir) / "tenant-acme"
            assert (workspace / "SOUL.md").exists()
            assert (workspace / "AGENTS.md").exists()

            # Agent added to config
            assert len(gw.patch_config_calls) == 1
            assert len(gw.agent_list) == 1
            assert gw.agent_list[0]["id"] == "tenant-acme"

    async def test_writes_custom_system_prompt_to_soul(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = MockGateway()
            p = TenantProvisioner(ProvisionerOptions(workspace_base_dir=tmpdir, gateway=gw))
            tenant = make_tenant(
                config=TenantConfig(
                    model_routing=ModelRoutingConfig(
                        primary="azure/gpt-4o", escalation_sentiment=-0.5
                    ),
                    confidence_threshold=0.7,
                    system_prompt="You are AcmeBot. Be helpful.",
                )
            )

            await p.provision(tenant)

            soul = (Path(tmpdir) / "tenant-acme" / "SOUL.md").read_text()
            assert soul == "You are AcmeBot. Be helpful."

    async def test_fails_if_agent_already_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = MockGateway()
            p = TenantProvisioner(ProvisionerOptions(workspace_base_dir=tmpdir, gateway=gw))
            tenant = make_tenant()

            # Provision once
            await p.provision(tenant)

            # Provision again — should fail
            result = await p.provision(tenant)
            assert result.status == "failed"
            assert "already exists" in (result.error or "")

    async def test_cleans_up_workspace_on_config_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = MockGateway()
            gw._fail_patch = True
            p = TenantProvisioner(ProvisionerOptions(workspace_base_dir=tmpdir, gateway=gw))
            tenant = make_tenant()

            result = await p.provision(tenant)

            assert result.status == "failed"

            # Workspace should be cleaned up
            workspace = Path(tmpdir) / "tenant-acme"
            assert not workspace.exists()


class TestDeprovision:
    async def test_removes_agent_and_archives_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = MockGateway()
            p = TenantProvisioner(ProvisionerOptions(workspace_base_dir=tmpdir, gateway=gw))
            tenant = make_tenant()

            # Provision first
            await p.provision(tenant)
            assert len(gw.agent_list) == 1

            # Deprovision
            result = await p.deprovision(tenant)

            assert result.status == "success"
            assert len(gw.agent_list) == 0

            # Original workspace gone
            workspace = Path(tmpdir) / "tenant-acme"
            assert not workspace.exists()

            # Archived workspace exists
            archived = [p for p in Path(tmpdir).iterdir() if "archived" in p.name]
            assert len(archived) == 1

    async def test_idempotent_for_nonexistent_agent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = MockGateway()
            p = TenantProvisioner(ProvisionerOptions(workspace_base_dir=tmpdir, gateway=gw))
            tenant = make_tenant()

            result = await p.deprovision(tenant)
            assert result.status == "success"


class TestGetMapping:
    def test_returns_correct_mapping(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = MockGateway()
            p = TenantProvisioner(ProvisionerOptions(workspace_base_dir=tmpdir, gateway=gw))
            tenant = make_tenant()

            mapping = p.get_mapping(tenant)

            assert mapping.tenant_id == "tenant-1"
            assert mapping.agent_id == "tenant-acme"
            assert mapping.workspace_path == str(Path(tmpdir) / "tenant-acme")
            assert mapping.agent_config.id == "tenant-acme"


# ─── Model Routing ────────────────────────────────────────


class TestModelRouting:
    def test_pick_model_returns_primary_for_text(self):
        from clawdesk.models import MessageAnalysis, pick_model

        config = ModelRoutingConfig(
            primary="openai-codex/gpt-5.3-codex-spark",
            vision_model="openai-codex/gpt-5.3-codex",
            escalation_sentiment=-0.5,
        )
        analysis = MessageAnalysis(has_images=False, estimated_tokens=500)

        assert pick_model(config, analysis) == "openai-codex/gpt-5.3-codex-spark"

    def test_pick_model_returns_vision_model_for_images(self):
        from clawdesk.models import MessageAnalysis, pick_model

        config = ModelRoutingConfig(
            primary="openai-codex/gpt-5.3-codex-spark",
            vision_model="openai-codex/gpt-5.3-codex",
            escalation_sentiment=-0.5,
        )
        analysis = MessageAnalysis(has_images=True, estimated_tokens=500)

        assert pick_model(config, analysis) == "openai-codex/gpt-5.3-codex"

    def test_pick_model_returns_primary_when_no_vision_model(self):
        from clawdesk.models import MessageAnalysis, pick_model

        config = ModelRoutingConfig(
            primary="azure/gpt-4o",
            escalation_sentiment=-0.5,
        )
        analysis = MessageAnalysis(has_images=True, estimated_tokens=500)

        assert pick_model(config, analysis) == "azure/gpt-4o"

    def test_pick_model_returns_long_context_model(self):
        from clawdesk.models import MessageAnalysis, pick_model

        config = ModelRoutingConfig(
            primary="openai-codex/gpt-5.3-codex-spark",
            long_context_model="openai-codex/gpt-5.3-codex",
            long_context_threshold=100_000,
            escalation_sentiment=-0.5,
        )
        analysis = MessageAnalysis(has_images=False, estimated_tokens=150_000)

        assert pick_model(config, analysis) == "openai-codex/gpt-5.3-codex"

    def test_vision_takes_priority_over_long_context(self):
        from clawdesk.models import MessageAnalysis, pick_model

        config = ModelRoutingConfig(
            primary="spark",
            vision_model="vision-model",
            long_context_model="long-model",
            long_context_threshold=100_000,
            escalation_sentiment=-0.5,
        )
        # Both triggers: images + long context
        analysis = MessageAnalysis(has_images=True, estimated_tokens=150_000)

        # Vision takes priority
        assert pick_model(config, analysis) == "vision-model"
