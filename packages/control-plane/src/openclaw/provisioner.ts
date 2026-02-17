/**
 * Tenant Provisioner
 *
 * Maps ClawDesk tenants to OpenClaw agents.
 * Creates/removes agent entries in the OpenClaw config via config.patch.
 *
 * Flow:
 *   createTenant(config) → generate agent ID → build agent config →
 *   create workspace → patch OpenClaw config → hot reload → done
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import type {
  Tenant,
  TenantConfig,
  OpenClawAgentConfig,
  OpenClawAgentMapping,
  TenantProvisioningResult,
  TenantDeprovisioningResult,
} from '@clawdesk/shared';
import type { GatewayClient } from './client.js';

export interface ProvisionerOptions {
  /** Base directory for tenant workspaces (e.g., /data/tenants) */
  workspaceBaseDir: string;
  /** OpenClaw gateway client */
  gateway: GatewayClient;
}

export class TenantProvisioner {
  private _workspaceBaseDir: string;
  private _gateway: GatewayClient;

  constructor(options: ProvisionerOptions) {
    this._workspaceBaseDir = options.workspaceBaseDir;
    this._gateway = options.gateway;
  }

  /**
   * Provision a new tenant:
   * 1. Generate OpenClaw agent ID from tenant slug
   * 2. Build agent config from tenant config
   * 3. Create workspace directory with bootstrap files
   * 4. Patch OpenClaw config to add the agent
   */
  async provision(tenant: Tenant): Promise<TenantProvisioningResult> {
    const agentId = this.tenantToAgentId(tenant.slug);
    const workspacePath = path.join(this._workspaceBaseDir, agentId);

    try {
      // 1. Create workspace
      await this._createWorkspace(workspacePath, tenant);

      // 2. Build agent config
      const agentConfig = this.buildAgentConfig(tenant, workspacePath);

      // 3. Patch OpenClaw config to add agent
      await this._addAgentToConfig(agentConfig);

      return {
        tenantId: tenant.id,
        agentId,
        workspacePath,
        status: 'success',
      };
    } catch (error) {
      // Clean up workspace on failure
      await fs.rm(workspacePath, { recursive: true, force: true }).catch(() => {});

      return {
        tenantId: tenant.id,
        agentId,
        workspacePath,
        status: 'failed',
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  /**
   * Deprovision a tenant:
   * 1. Remove agent from OpenClaw config
   * 2. Archive workspace directory
   */
  async deprovision(tenant: Tenant): Promise<TenantDeprovisioningResult> {
    const agentId = this.tenantToAgentId(tenant.slug);

    try {
      // 1. Remove agent from OpenClaw config
      await this._removeAgentFromConfig(agentId);

      // 2. Archive workspace (rename, don't delete)
      const workspacePath = path.join(this._workspaceBaseDir, agentId);
      const archivePath = `${workspacePath}.archived.${Date.now()}`;
      await fs.rename(workspacePath, archivePath).catch(() => {});

      return { tenantId: tenant.id, status: 'success' };
    } catch (error) {
      return {
        tenantId: tenant.id,
        status: 'failed',
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  /**
   * Convert a tenant slug to an OpenClaw agent ID.
   * Prefix with "tenant-" to avoid collisions with built-in agents.
   */
  tenantToAgentId(slug: string): string {
    return `tenant-${slug}`;
  }

  /**
   * Build an OpenClaw agent config entry from a tenant.
   */
  buildAgentConfig(tenant: Tenant, workspacePath: string): OpenClawAgentConfig {
    const model = tenant.config.modelRouting.fallbacks?.length
      ? {
          primary: tenant.config.modelRouting.primary,
          fallbacks: tenant.config.modelRouting.fallbacks,
        }
      : tenant.config.modelRouting.primary;

    return {
      id: this.tenantToAgentId(tenant.slug),
      name: tenant.name,
      workspace: workspacePath,
      model,
      sandbox: {
        mode: 'all',
        workspaceAccess: 'rw',
      },
    };
  }

  /**
   * Get the full mapping between a tenant and its OpenClaw agent.
   */
  getMapping(tenant: Tenant): OpenClawAgentMapping {
    const agentId = this.tenantToAgentId(tenant.slug);
    const workspacePath = path.join(this._workspaceBaseDir, agentId);

    return {
      tenantId: tenant.id,
      agentId,
      workspacePath,
      agentConfig: this.buildAgentConfig(tenant, workspacePath),
    };
  }

  // ─── Private ─────────────────────────────────────────

  /**
   * Create tenant workspace with bootstrap files.
   * OpenClaw will read these when the agent starts.
   */
  private async _createWorkspace(workspacePath: string, tenant: Tenant): Promise<void> {
    await fs.mkdir(workspacePath, { recursive: true });

    // SOUL.md — defines the AI agent's persona
    const soulContent = tenant.config.systemPrompt
      ? tenant.config.systemPrompt
      : `# ${tenant.name} Support Agent\n\nYou are a helpful customer support agent for ${tenant.name}.\nBe friendly, professional, and concise.\nIf you're unsure about something, say so honestly.\n`;

    await fs.writeFile(path.join(workspacePath, 'SOUL.md'), soulContent);

    // AGENTS.md — workspace instructions
    await fs.writeFile(
      path.join(workspacePath, 'AGENTS.md'),
      `# ${tenant.name} — ClawDesk Agent\n\nThis workspace is managed by ClawDesk.\nTenant ID: ${tenant.id}\n`,
    );
  }

  /**
   * Add an agent to the OpenClaw config via config.patch.
   */
  private async _addAgentToConfig(agentConfig: OpenClawAgentConfig): Promise<void> {
    const snapshot = await this._gateway.getConfig();
    const config = snapshot.config;

    // Get existing agents list
    const agents = (config.agents as Record<string, unknown>) ?? {};
    const list = (agents.list as OpenClawAgentConfig[]) ?? [];

    // Check for duplicate
    if (list.some((a) => a.id === agentConfig.id)) {
      throw new Error(`Agent ${agentConfig.id} already exists`);
    }

    // Patch: add new agent to list
    await this._gateway.patchConfig(
      {
        agents: {
          ...agents,
          list: [...list, agentConfig],
        },
      },
      snapshot.hash,
    );
  }

  /**
   * Remove an agent from the OpenClaw config via config.patch.
   */
  private async _removeAgentFromConfig(agentId: string): Promise<void> {
    const snapshot = await this._gateway.getConfig();
    const config = snapshot.config;

    const agents = (config.agents as Record<string, unknown>) ?? {};
    const list = (agents.list as OpenClawAgentConfig[]) ?? [];

    // Filter out the agent
    const filtered = list.filter((a) => a.id !== agentId);

    if (filtered.length === list.length) {
      // Agent wasn't in the list — idempotent, not an error
      return;
    }

    await this._gateway.patchConfig(
      {
        agents: {
          ...agents,
          list: filtered,
        },
      },
      snapshot.hash,
    );
  }
}
