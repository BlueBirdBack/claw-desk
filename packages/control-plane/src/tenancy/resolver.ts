/**
 * TenantResolverChain — resolves tenant identity from request context.
 *
 * Chain of responsibility: tries each resolver in order, first match wins.
 * Results are cached per-request to avoid repeated DB lookups.
 */

import type { TenantResolver, RequestContext } from '@clawdesk/shared';

export class TenantResolverChain {
  private _resolvers: TenantResolver[];

  constructor(resolvers: TenantResolver[]) {
    this._resolvers = resolvers;
  }

  /**
   * Resolve tenant ID from request context.
   * Tries each resolver in order. First non-null result wins.
   * Returns null if no resolver can identify the tenant.
   */
  async resolve(context: RequestContext): Promise<string | null> {
    for (const resolver of this._resolvers) {
      const tenantId = await resolver.resolve(context);
      if (tenantId !== null) {
        return tenantId;
      }
    }
    return null;
  }

  /** Get the list of resolver names (for debugging/logging). */
  get resolverNames(): string[] {
    return this._resolvers.map((r) => r.name);
  }
}

// ─── Built-in Resolvers ──────────────────────────────────

/**
 * Resolves tenant from a request header (e.g., X-Tenant-ID or X-API-Key).
 */
export class HeaderTenantResolver implements TenantResolver {
  readonly name = 'header';
  private _headerName: string;
  private _lookupFn: (value: string) => Promise<string | null>;

  constructor(
    headerName: string,
    lookupFn: (headerValue: string) => Promise<string | null>,
  ) {
    this._headerName = headerName.toLowerCase();
    this._lookupFn = lookupFn;
  }

  async resolve(context: RequestContext): Promise<string | null> {
    const value = context.headers[this._headerName];
    if (!value) return null;
    return this._lookupFn(value);
  }
}

/**
 * Resolves tenant from subdomain (e.g., acme.clawdesk.com → "acme").
 */
export class SubdomainTenantResolver implements TenantResolver {
  readonly name = 'subdomain';
  private _rootDomain: string;
  private _lookupFn: (slug: string) => Promise<string | null>;

  constructor(
    rootDomain: string,
    lookupFn: (slug: string) => Promise<string | null>,
  ) {
    this._rootDomain = rootDomain.toLowerCase();
    this._lookupFn = lookupFn;
  }

  async resolve(context: RequestContext): Promise<string | null> {
    const hostname = context.hostname?.toLowerCase();
    if (!hostname) return null;

    // Skip if it's the root domain itself
    if (hostname === this._rootDomain || hostname === `www.${this._rootDomain}`) {
      return null;
    }

    // Skip IP addresses
    if (/^\d+\.\d+\.\d+\.\d+$/.test(hostname)) {
      return null;
    }

    // Extract subdomain
    if (!hostname.endsWith(`.${this._rootDomain}`)) {
      return null;
    }

    const subdomain = hostname.replace(`.${this._rootDomain}`, '');
    if (!subdomain || subdomain.includes('.')) {
      return null; // Skip multi-level subdomains
    }

    return this._lookupFn(subdomain);
  }
}

/**
 * Resolves tenant from JWT claims (e.g., tenant_id or org_id claim).
 */
export class JwtClaimTenantResolver implements TenantResolver {
  readonly name = 'jwt-claim';
  private _claimName: string;

  constructor(claimName: string = 'tenant_id') {
    this._claimName = claimName;
  }

  async resolve(context: RequestContext): Promise<string | null> {
    const value = context.claims?.[this._claimName];
    if (typeof value === 'string' && value.length > 0) {
      return value;
    }
    return null;
  }
}
