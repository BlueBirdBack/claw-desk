"""
Tenant Provisioner

Maps ClawDesk tenants to OpenClaw agents.
Creates/removes agent entries in the OpenClaw config via config.patch.

Flow:
  create_tenant(config) → generate agent ID → build agent config →
  create workspace → patch OpenClaw config → hot reload → done
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from clawdesk.models import (
    OpenClawAgentConfig,
    OpenClawAgentMapping,
    Tenant,
    TenantDeprovisioningResult,
    TenantProvisioningResult,
)


class GatewayClientProtocol(Protocol):
    """Minimal gateway client interface for provisioning."""

    async def get_config(self) -> Any: ...
    async def patch_config(self, patch: dict, base_hash: str) -> None: ...


@dataclass
class ProvisionerOptions:
    workspace_base_dir: str
    gateway: GatewayClientProtocol


class TenantProvisioner:
    def __init__(self, options: ProvisionerOptions) -> None:
        self._workspace_base_dir = Path(options.workspace_base_dir)
        self._gateway = options.gateway

    def tenant_to_agent_id(self, slug: str) -> str:
        """Convert a tenant slug to an OpenClaw agent ID."""
        return f"tenant-{slug}"

    def build_agent_config(self, tenant: Tenant, workspace_path: str) -> OpenClawAgentConfig:
        """Build an OpenClaw agent config entry from a tenant."""
        routing = tenant.config.model_routing

        if routing.fallbacks:
            model: str | dict[str, Any] = {
                "primary": routing.primary,
                "fallbacks": routing.fallbacks,
            }
        else:
            model = routing.primary

        return OpenClawAgentConfig(
            id=self.tenant_to_agent_id(tenant.slug),
            name=tenant.name,
            workspace=workspace_path,
            model=model,
            sandbox={"mode": "all", "workspaceAccess": "rw"},
        )

    def get_mapping(self, tenant: Tenant) -> OpenClawAgentMapping:
        """Get the full mapping between a tenant and its OpenClaw agent."""
        agent_id = self.tenant_to_agent_id(tenant.slug)
        workspace_path = str(self._workspace_base_dir / agent_id)

        return OpenClawAgentMapping(
            tenant_id=tenant.id,
            agent_id=agent_id,
            workspace_path=workspace_path,
            agent_config=self.build_agent_config(tenant, workspace_path),
        )

    async def provision(self, tenant: Tenant) -> TenantProvisioningResult:
        """
        Provision a new tenant:
        1. Generate OpenClaw agent ID from tenant slug
        2. Build agent config from tenant config
        3. Create workspace directory with bootstrap files
        4. Patch OpenClaw config to add the agent
        """
        agent_id = self.tenant_to_agent_id(tenant.slug)
        workspace_path = self._workspace_base_dir / agent_id

        try:
            # 1. Create workspace
            self._create_workspace(workspace_path, tenant)

            # 2. Build agent config
            agent_config = self.build_agent_config(tenant, str(workspace_path))

            # 3. Patch OpenClaw config
            await self._add_agent_to_config(agent_config)

            return TenantProvisioningResult(
                tenant_id=tenant.id,
                agent_id=agent_id,
                workspace_path=str(workspace_path),
                status="success",
            )
        except Exception as e:
            # Clean up workspace on failure
            shutil.rmtree(workspace_path, ignore_errors=True)

            return TenantProvisioningResult(
                tenant_id=tenant.id,
                agent_id=agent_id,
                workspace_path=str(workspace_path),
                status="failed",
                error=str(e),
            )

    async def deprovision(self, tenant: Tenant) -> TenantDeprovisioningResult:
        """
        Deprovision a tenant:
        1. Remove agent from OpenClaw config
        2. Archive workspace directory
        """
        agent_id = self.tenant_to_agent_id(tenant.slug)

        try:
            # 1. Remove agent from config
            await self._remove_agent_from_config(agent_id)

            # 2. Archive workspace (rename, don't delete)
            workspace_path = self._workspace_base_dir / agent_id
            if workspace_path.exists():
                import time

                archive_path = workspace_path.with_suffix(f".archived.{int(time.time())}")
                workspace_path.rename(archive_path)

            return TenantDeprovisioningResult(tenant_id=tenant.id, status="success")
        except Exception as e:
            return TenantDeprovisioningResult(
                tenant_id=tenant.id, status="failed", error=str(e)
            )

    # ─── Private ─────────────────────────────────────────

    def _create_workspace(self, workspace_path: Path, tenant: Tenant) -> None:
        """Create tenant workspace with bootstrap files."""
        workspace_path.mkdir(parents=True, exist_ok=True)

        # SOUL.md — AI agent persona
        if tenant.config.system_prompt:
            soul_content = tenant.config.system_prompt
        else:
            soul_content = (
                f"# {tenant.name} Support Agent\n\n"
                f"You are a helpful customer support agent for {tenant.name}.\n"
                f"Be friendly, professional, and concise.\n"
                f"If you're unsure about something, say so honestly.\n"
            )
        (workspace_path / "SOUL.md").write_text(soul_content)

        # AGENTS.md — workspace instructions
        (workspace_path / "AGENTS.md").write_text(
            f"# {tenant.name} — ClawDesk Agent\n\n"
            f"This workspace is managed by ClawDesk.\n"
            f"Tenant ID: {tenant.id}\n"
        )

    async def _add_agent_to_config(self, agent_config: OpenClawAgentConfig) -> None:
        """Add an agent to the OpenClaw config via config.patch."""
        snapshot = await self._gateway.get_config()
        config = snapshot.config

        agents = config.get("agents", {})
        agent_list: list[dict] = agents.get("list", [])

        # Check for duplicate
        if any(a.get("id") == agent_config.id for a in agent_list):
            raise ValueError(f"Agent {agent_config.id} already exists")

        await self._gateway.patch_config(
            {
                "agents": {
                    **agents,
                    "list": [*agent_list, agent_config.model_dump(exclude_none=True)],
                },
            },
            snapshot.hash,
        )

    async def _remove_agent_from_config(self, agent_id: str) -> None:
        """Remove an agent from the OpenClaw config via config.patch."""
        snapshot = await self._gateway.get_config()
        config = snapshot.config

        agents = config.get("agents", {})
        agent_list: list[dict] = agents.get("list", [])

        filtered = [a for a in agent_list if a.get("id") != agent_id]

        if len(filtered) == len(agent_list):
            return  # Agent wasn't there — idempotent

        await self._gateway.patch_config(
            {"agents": {**agents, "list": filtered}},
            snapshot.hash,
        )
