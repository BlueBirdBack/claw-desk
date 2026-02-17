import { describe, it, expect, vi, beforeEach } from 'vitest';
import { TenancyContext } from '../context';
import type { Tenant, TenancyBootstrapper } from '@clawdesk/shared';

// ─── Helpers ─────────────────────────────────────────────

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

function makeBootstrapper(name: string, log: string[]): TenancyBootstrapper {
  return {
    name,
    bootstrap: vi.fn(async () => { log.push(`${name}:up`); }),
    revert: vi.fn(async () => { log.push(`${name}:down`); }),
  };
}

function makeFailingBootstrapper(name: string, log: string[]): TenancyBootstrapper {
  return {
    name,
    bootstrap: vi.fn(async () => {
      log.push(`${name}:up`);
      throw new Error(`${name} failed`);
    }),
    revert: vi.fn(async () => { log.push(`${name}:down`); }),
  };
}

// ─── Tests ───────────────────────────────────────────────

describe('TenancyContext', () => {
  let log: string[];

  beforeEach(() => {
    log = [];
  });

  describe('initialize', () => {
    it('runs all bootstrappers in order', async () => {
      const ctx = new TenancyContext([
        makeBootstrapper('A', log),
        makeBootstrapper('B', log),
        makeBootstrapper('C', log),
      ]);

      await ctx.initialize(makeTenant());

      expect(log).toEqual(['A:up', 'B:up', 'C:up']);
      expect(ctx.initialized).toBe(true);
      expect(ctx.tenant?.id).toBe('tenant-1');
    });

    it('is a no-op if already initialized for the same tenant', async () => {
      const ctx = new TenancyContext([makeBootstrapper('A', log)]);
      const tenant = makeTenant();

      await ctx.initialize(tenant);
      await ctx.initialize(tenant);

      expect(log).toEqual(['A:up']); // Only once
    });

    it('ends previous tenancy before initializing new tenant', async () => {
      const ctx = new TenancyContext([makeBootstrapper('A', log)]);

      await ctx.initialize(makeTenant({ id: 'tenant-1' }));
      await ctx.initialize(makeTenant({ id: 'tenant-2' }));

      expect(log).toEqual(['A:up', 'A:down', 'A:up']);
      expect(ctx.tenant?.id).toBe('tenant-2');
    });
  });

  describe('end', () => {
    it('reverts bootstrappers in reverse order', async () => {
      const ctx = new TenancyContext([
        makeBootstrapper('A', log),
        makeBootstrapper('B', log),
        makeBootstrapper('C', log),
      ]);

      await ctx.initialize(makeTenant());
      log.length = 0;

      await ctx.end();

      expect(log).toEqual(['C:down', 'B:down', 'A:down']);
      expect(ctx.initialized).toBe(false);
      expect(ctx.tenant).toBeNull();
    });

    it('is a no-op if not initialized', async () => {
      const ctx = new TenancyContext([makeBootstrapper('A', log)]);

      await ctx.end();

      expect(log).toEqual([]);
    });
  });

  describe('error handling', () => {
    it('reverts previously initialized bootstrappers if one fails', async () => {
      const ctx = new TenancyContext([
        makeBootstrapper('A', log),
        makeBootstrapper('B', log),
        makeFailingBootstrapper('C', log),
        makeBootstrapper('D', log),
      ]);

      await expect(ctx.initialize(makeTenant())).rejects.toThrow('C failed');

      expect(log).toEqual(['A:up', 'B:up', 'C:up', 'B:down', 'A:down']);
      expect(ctx.initialized).toBe(false);
      expect(ctx.tenant).toBeNull();
    });

    it('does not revert bootstrappers that were never initialized', async () => {
      const bootstrapperD = makeBootstrapper('D', log);
      const ctx = new TenancyContext([
        makeBootstrapper('A', log),
        makeFailingBootstrapper('B', log),
        makeBootstrapper('C', log),
        bootstrapperD,
      ]);

      await expect(ctx.initialize(makeTenant())).rejects.toThrow('B failed');

      expect(bootstrapperD.revert).not.toHaveBeenCalled();
    });
  });

  describe('run', () => {
    it('initializes, runs callback, then reverts', async () => {
      const ctx = new TenancyContext([makeBootstrapper('A', log)]);
      const tenant = makeTenant();

      const result = await ctx.run(tenant, async (t) => {
        log.push('callback');
        return t.name;
      });

      expect(result).toBe('Acme Corp');
      expect(log).toEqual(['A:up', 'callback', 'A:down']);
      expect(ctx.initialized).toBe(false);
    });

    it('reverts even if callback throws', async () => {
      const ctx = new TenancyContext([makeBootstrapper('A', log)]);

      await expect(
        ctx.run(makeTenant(), async () => {
          log.push('callback');
          throw new Error('callback boom');
        }),
      ).rejects.toThrow('callback boom');

      expect(log).toEqual(['A:up', 'callback', 'A:down']);
      expect(ctx.initialized).toBe(false);
    });

    it('restores previous tenant context after run', async () => {
      const ctx = new TenancyContext([makeBootstrapper('A', log)]);

      const tenantA = makeTenant({ id: 'tenant-a', name: 'Tenant A' });
      const tenantB = makeTenant({ id: 'tenant-b', name: 'Tenant B' });

      await ctx.initialize(tenantA);
      log.length = 0;

      await ctx.run(tenantB, async () => {
        expect(ctx.tenant?.id).toBe('tenant-b');
      });

      expect(ctx.tenant?.id).toBe('tenant-a');
      expect(ctx.initialized).toBe(true);
    });
  });

  describe('central', () => {
    it('reverts to central context, then restores previous tenant', async () => {
      const ctx = new TenancyContext([makeBootstrapper('A', log)]);
      const tenant = makeTenant();

      await ctx.initialize(tenant);
      log.length = 0;

      await ctx.central(async (prev) => {
        expect(prev?.id).toBe('tenant-1');
        expect(ctx.initialized).toBe(false);
        log.push('central-work');
      });

      expect(ctx.tenant?.id).toBe('tenant-1');
      expect(log).toEqual(['A:down', 'central-work', 'A:up']);
    });
  });

  describe('runForMultiple', () => {
    it('runs callback for each tenant and restores original context', async () => {
      const ctx = new TenancyContext([makeBootstrapper('A', log)]);
      const tenants = [
        makeTenant({ id: 't1', name: 'T1' }),
        makeTenant({ id: 't2', name: 'T2' }),
        makeTenant({ id: 't3', name: 'T3' }),
      ];

      const visited: string[] = [];
      await ctx.runForMultiple(tenants, async (t) => {
        visited.push(t.id);
      });

      expect(visited).toEqual(['t1', 't2', 't3']);
      expect(ctx.initialized).toBe(false);
      expect(ctx.tenant).toBeNull();
    });
  });
});
