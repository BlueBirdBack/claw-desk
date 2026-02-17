import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { TenantProvisioner } from '../provisioner';
import type { Tenant, OpenClawAgentConfig } from '@clawdesk/shared';

// ─── Mock Gateway Client ─────────────────────────────────

function makeMockGateway() {
  const currentConfig = {
    agents: {
      list: [] as OpenClawAgentConfig[],
    },
  };
  let hash = 'hash-1';

  return {
    getConfig: vi.fn(async () => ({
      config: JSON.parse(JSON.stringify(currentConfig)),
      hash,
    })),
    patchConfig: vi.fn(async (patch: Record<string, unknown>, baseHash: string) => {
      if (baseHash !== hash) {
        throw new Error('Config hash mismatch');
      }
      // Apply the patch
      if (patch.agents) {
        currentConfig.agents = patch.agents as typeof currentConfig.agents;
      }
      hash = `hash-${Date.now()}`;
    }),
    chatSend: vi.fn(),
    chatHistory: vi.fn(),
    sessionsList: vi.fn(),
    connect: vi.fn(),
    disconnect: vi.fn(),

    // Test helpers
    _getAgentList: () => currentConfig.agents.list,
  };
}

function makeTenant(overrides: Partial<Tenant> = {}): Tenant {
  return {
    id: 'tenant-1',
    name: 'Acme Corp',
    slug: 'acme',
    status: 'active',
    openclawAgentId: 'tenant-acme',
    config: {
      modelRouting: {
        primary: 'azure/gpt-4o',
        escalationSentiment: -0.5,
      },
      confidenceThreshold: 0.7,
    },
    billing: {
      plan: 'pro',
      usageThisMonth: { conversations: 0, inputTokens: 0, outputTokens: 0, knowledgeBaseQueries: 0 },
    },
    createdAt: new Date(),
    updatedAt: new Date(),
    ...overrides,
  };
}

// ─── Tests ───────────────────────────────────────────────

describe('TenantProvisioner', () => {
  let tmpDir: string;
  let gateway: ReturnType<typeof makeMockGateway>;
  let provisioner: TenantProvisioner;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'clawdesk-test-'));
    gateway = makeMockGateway();
    provisioner = new TenantProvisioner({
      workspaceBaseDir: tmpDir,
      gateway: gateway as any,
    });
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  describe('tenantToAgentId', () => {
    it('prefixes tenant slug with "tenant-"', () => {
      expect(provisioner.tenantToAgentId('acme')).toBe('tenant-acme');
      expect(provisioner.tenantToAgentId('my-corp')).toBe('tenant-my-corp');
    });
  });

  describe('buildAgentConfig', () => {
    it('builds correct agent config from tenant', () => {
      const tenant = makeTenant();
      const config = provisioner.buildAgentConfig(tenant, '/data/tenants/tenant-acme');

      expect(config).toEqual({
        id: 'tenant-acme',
        name: 'Acme Corp',
        workspace: '/data/tenants/tenant-acme',
        model: 'azure/gpt-4o',
        sandbox: {
          mode: 'all',
          workspaceAccess: 'rw',
        },
      });
    });

    it('includes fallbacks when specified', () => {
      const tenant = makeTenant({
        config: {
          modelRouting: {
            primary: 'azure/gpt-4o',
            fallbacks: ['azure/gpt-4o-mini'],
            escalationSentiment: -0.5,
          },
          confidenceThreshold: 0.7,
        },
      });

      const config = provisioner.buildAgentConfig(tenant, '/data/tenants/tenant-acme');

      expect(config.model).toEqual({
        primary: 'azure/gpt-4o',
        fallbacks: ['azure/gpt-4o-mini'],
      });
    });
  });

  describe('provision', () => {
    it('creates workspace and patches OpenClaw config', async () => {
      const tenant = makeTenant();
      const result = await provisioner.provision(tenant);

      expect(result.status).toBe('success');
      expect(result.agentId).toBe('tenant-acme');

      // Verify workspace was created
      const soulExists = await fs.access(path.join(tmpDir, 'tenant-acme', 'SOUL.md'))
        .then(() => true).catch(() => false);
      expect(soulExists).toBe(true);

      const agentsExists = await fs.access(path.join(tmpDir, 'tenant-acme', 'AGENTS.md'))
        .then(() => true).catch(() => false);
      expect(agentsExists).toBe(true);

      // Verify agent was added to config
      expect(gateway.patchConfig).toHaveBeenCalledOnce();
      const agentList = gateway._getAgentList();
      expect(agentList).toHaveLength(1);
      expect(agentList[0].id).toBe('tenant-acme');
    });

    it('writes custom system prompt to SOUL.md', async () => {
      const tenant = makeTenant({
        config: {
          modelRouting: { primary: 'azure/gpt-4o', escalationSentiment: -0.5 },
          confidenceThreshold: 0.7,
          systemPrompt: 'You are AcmeBot. Be helpful.',
        },
      });

      await provisioner.provision(tenant);

      const soul = await fs.readFile(path.join(tmpDir, 'tenant-acme', 'SOUL.md'), 'utf-8');
      expect(soul).toBe('You are AcmeBot. Be helpful.');
    });

    it('fails if agent already exists', async () => {
      const tenant = makeTenant();

      // Provision once
      await provisioner.provision(tenant);

      // Provision again — should fail
      const result = await provisioner.provision(tenant);
      expect(result.status).toBe('failed');
      expect(result.error).toContain('already exists');
    });

    it('cleans up workspace on config patch failure', async () => {
      gateway.patchConfig.mockRejectedValueOnce(new Error('config write failed'));

      const tenant = makeTenant();
      const result = await provisioner.provision(tenant);

      expect(result.status).toBe('failed');

      // Workspace should be cleaned up
      const exists = await fs.access(path.join(tmpDir, 'tenant-acme'))
        .then(() => true).catch(() => false);
      expect(exists).toBe(false);
    });
  });

  describe('deprovision', () => {
    it('removes agent from config and archives workspace', async () => {
      const tenant = makeTenant();

      // First provision
      await provisioner.provision(tenant);
      expect(gateway._getAgentList()).toHaveLength(1);

      // Then deprovision
      const result = await provisioner.deprovision(tenant);

      expect(result.status).toBe('success');
      expect(gateway._getAgentList()).toHaveLength(0);

      // Workspace should be archived (renamed), not deleted
      const originalExists = await fs.access(path.join(tmpDir, 'tenant-acme'))
        .then(() => true).catch(() => false);
      expect(originalExists).toBe(false);

      // Archived dir should exist
      const entries = await fs.readdir(tmpDir);
      const archived = entries.find(e => e.startsWith('tenant-acme.archived.'));
      expect(archived).toBeDefined();
    });

    it('is idempotent — deprovisioning non-existent agent succeeds', async () => {
      const tenant = makeTenant();
      const result = await provisioner.deprovision(tenant);

      expect(result.status).toBe('success');
    });
  });

  describe('getMapping', () => {
    it('returns correct mapping between tenant and OpenClaw agent', () => {
      const tenant = makeTenant();
      const mapping = provisioner.getMapping(tenant);

      expect(mapping.tenantId).toBe('tenant-1');
      expect(mapping.agentId).toBe('tenant-acme');
      expect(mapping.workspacePath).toBe(path.join(tmpDir, 'tenant-acme'));
      expect(mapping.agentConfig.id).toBe('tenant-acme');
    });
  });
});
