import { describe, it, expect } from 'vitest';
import {
  TenantResolverChain,
  HeaderTenantResolver,
  SubdomainTenantResolver,
  JwtClaimTenantResolver,
} from '../resolver';
import type { RequestContext } from '@clawdesk/shared';

// ─── Helpers ─────────────────────────────────────────────

function makeContext(overrides: Partial<RequestContext> = {}): RequestContext {
  return {
    headers: {},
    ...overrides,
  };
}

const directLookup = async (value: string) => value; // passthrough
const mappedLookup = async (slug: string) => slug === 'acme' ? 'tenant-acme' : null;

// ─── TenantResolverChain ─────────────────────────────────

describe('TenantResolverChain', () => {
  it('returns first non-null result', async () => {
    const chain = new TenantResolverChain([
      new HeaderTenantResolver('x-tenant-id', directLookup),
      new SubdomainTenantResolver('clawdesk.com', mappedLookup),
    ]);

    const result = await chain.resolve(
      makeContext({ headers: { 'x-tenant-id': 'tenant-123' } }),
    );

    expect(result).toBe('tenant-123');
  });

  it('falls through to second resolver if first returns null', async () => {
    const chain = new TenantResolverChain([
      new HeaderTenantResolver('x-tenant-id', directLookup),
      new SubdomainTenantResolver('clawdesk.com', mappedLookup),
    ]);

    const result = await chain.resolve(
      makeContext({ headers: {}, hostname: 'acme.clawdesk.com' }),
    );

    expect(result).toBe('tenant-acme');
  });

  it('returns null if no resolver matches', async () => {
    const chain = new TenantResolverChain([
      new HeaderTenantResolver('x-tenant-id', directLookup),
    ]);

    const result = await chain.resolve(makeContext());

    expect(result).toBeNull();
  });

  it('exposes resolver names for debugging', () => {
    const chain = new TenantResolverChain([
      new HeaderTenantResolver('x-tenant-id', directLookup),
      new SubdomainTenantResolver('clawdesk.com', mappedLookup),
      new JwtClaimTenantResolver('org_id'),
    ]);

    expect(chain.resolverNames).toEqual(['header', 'subdomain', 'jwt-claim']);
  });
});

// ─── HeaderTenantResolver ────────────────────────────────

describe('HeaderTenantResolver', () => {
  it('resolves tenant from header value', async () => {
    const resolver = new HeaderTenantResolver('x-api-key', async (key) =>
      key === 'sk-acme-123' ? 'tenant-acme' : null,
    );

    const result = await resolver.resolve(
      makeContext({ headers: { 'x-api-key': 'sk-acme-123' } }),
    );

    expect(result).toBe('tenant-acme');
  });

  it('returns null if header is missing', async () => {
    const resolver = new HeaderTenantResolver('x-api-key', directLookup);

    const result = await resolver.resolve(makeContext());

    expect(result).toBeNull();
  });

  it('is case-insensitive for header names', async () => {
    const resolver = new HeaderTenantResolver('X-Tenant-ID', directLookup);

    const result = await resolver.resolve(
      makeContext({ headers: { 'x-tenant-id': 'tenant-123' } }),
    );

    expect(result).toBe('tenant-123');
  });
});

// ─── SubdomainTenantResolver ─────────────────────────────

describe('SubdomainTenantResolver', () => {
  const resolver = new SubdomainTenantResolver('clawdesk.com', mappedLookup);

  it('extracts subdomain and resolves tenant', async () => {
    const result = await resolver.resolve(
      makeContext({ hostname: 'acme.clawdesk.com' }),
    );

    expect(result).toBe('tenant-acme');
  });

  it('returns null for root domain', async () => {
    const result = await resolver.resolve(
      makeContext({ hostname: 'clawdesk.com' }),
    );

    expect(result).toBeNull();
  });

  it('returns null for www subdomain', async () => {
    const result = await resolver.resolve(
      makeContext({ hostname: 'www.clawdesk.com' }),
    );

    expect(result).toBeNull();
  });

  it('returns null for IP addresses', async () => {
    const result = await resolver.resolve(
      makeContext({ hostname: '192.168.1.100' }),
    );

    expect(result).toBeNull();
  });

  it('returns null for unrelated domains', async () => {
    const result = await resolver.resolve(
      makeContext({ hostname: 'acme.example.com' }),
    );

    expect(result).toBeNull();
  });

  it('returns null for unknown subdomains', async () => {
    const result = await resolver.resolve(
      makeContext({ hostname: 'unknown.clawdesk.com' }),
    );

    expect(result).toBeNull(); // mappedLookup returns null for unknown slugs
  });

  it('returns null for multi-level subdomains', async () => {
    const result = await resolver.resolve(
      makeContext({ hostname: 'a.b.clawdesk.com' }),
    );

    expect(result).toBeNull();
  });

  it('returns null if hostname is missing', async () => {
    const result = await resolver.resolve(makeContext());

    expect(result).toBeNull();
  });
});

// ─── JwtClaimTenantResolver ──────────────────────────────

describe('JwtClaimTenantResolver', () => {
  it('resolves tenant from default claim name', async () => {
    const resolver = new JwtClaimTenantResolver();

    const result = await resolver.resolve(
      makeContext({ claims: { tenant_id: 'tenant-abc' } }),
    );

    expect(result).toBe('tenant-abc');
  });

  it('resolves tenant from custom claim name', async () => {
    const resolver = new JwtClaimTenantResolver('org_id');

    const result = await resolver.resolve(
      makeContext({ claims: { org_id: 'org-xyz' } }),
    );

    expect(result).toBe('org-xyz');
  });

  it('returns null if claim is missing', async () => {
    const resolver = new JwtClaimTenantResolver();

    const result = await resolver.resolve(
      makeContext({ claims: { other: 'value' } }),
    );

    expect(result).toBeNull();
  });

  it('returns null if claims object is missing', async () => {
    const resolver = new JwtClaimTenantResolver();

    const result = await resolver.resolve(makeContext());

    expect(result).toBeNull();
  });

  it('returns null for non-string claim values', async () => {
    const resolver = new JwtClaimTenantResolver();

    const result = await resolver.resolve(
      makeContext({ claims: { tenant_id: 12345 } }),
    );

    expect(result).toBeNull();
  });
});
