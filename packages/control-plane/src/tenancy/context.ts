/**
 * TenancyContext — the central context manager.
 *
 * Manages the current tenant context and orchestrates the bootstrapper chain.
 * Inspired by stancl/tenancy's Tenancy class.
 *
 * Key behaviors:
 * - initialize(tenant) → runs all bootstrappers in order
 * - end() → reverts all bootstrappers in reverse order
 * - run(tenant, fn) → atomic: initialize, run callback, revert (even on error)
 * - Only reverts bootstrappers that were actually initialized
 * - If a bootstrapper fails during initialization, reverts all previously initialized ones
 */

import type { Tenant, TenancyBootstrapper } from '@clawdesk/shared';

export class TenancyContext {
  private _tenant: Tenant | null = null;
  private _initialized = false;
  private _initializedBootstrappers: TenancyBootstrapper[] = [];
  private _bootstrappers: TenancyBootstrapper[];

  constructor(bootstrappers: TenancyBootstrapper[]) {
    this._bootstrappers = bootstrappers;
  }

  /** The current tenant, or null if in central context. */
  get tenant(): Tenant | null {
    return this._tenant;
  }

  /** Whether tenancy is currently initialized. */
  get initialized(): boolean {
    return this._initialized;
  }

  /** Initialize tenancy for the given tenant. */
  async initialize(tenant: Tenant): Promise<void> {
    if (this._initialized && this._tenant?.id === tenant.id) {
      return;
    }

    if (this._initialized) {
      await this.end();
    }

    this._tenant = tenant;
    this._initializedBootstrappers = [];

    for (const bootstrapper of this._bootstrappers) {
      try {
        await bootstrapper.bootstrap(tenant);
        this._initializedBootstrappers.push(bootstrapper);
      } catch (error) {
        await this._revertInitialized();
        this._tenant = null;
        this._initialized = false;
        throw error;
      }
    }

    this._initialized = true;
  }

  /** End tenancy, reverting to central context. */
  async end(): Promise<void> {
    if (!this._initialized) {
      return;
    }

    await this._revertInitialized();

    this._tenant = null;
    this._initialized = false;
    this._initializedBootstrappers = [];
  }

  /**
   * Run a callback in a tenant's context. Atomic — always reverts.
   */
  async run<T>(tenant: Tenant, callback: (tenant: Tenant) => Promise<T>): Promise<T> {
    const previousTenant = this._tenant;
    let result: T;

    try {
      await this.initialize(tenant);
      result = await callback(tenant);
    } finally {
      if (previousTenant) {
        await this.initialize(previousTenant);
      } else {
        await this.end();
      }
    }

    return result;
  }

  /**
   * Run a callback in the central (non-tenant) context. Atomic — restores previous context.
   */
  async central<T>(callback: (previousTenant: Tenant | null) => Promise<T>): Promise<T> {
    const previousTenant = this._tenant;

    await this.end();

    const result = await callback(previousTenant);

    if (previousTenant) {
      await this.initialize(previousTenant);
    }

    return result;
  }

  /**
   * Run a callback for multiple tenants sequentially.
   * Restores original context after all tenants are processed.
   */
  async runForMultiple(
    tenants: Tenant[],
    callback: (tenant: Tenant) => Promise<void>,
  ): Promise<void> {
    const originalTenant = this._tenant;

    for (const tenant of tenants) {
      await this.initialize(tenant);
      await callback(tenant);
    }

    if (originalTenant) {
      await this.initialize(originalTenant);
    } else {
      await this.end();
    }
  }

  /** Revert initialized bootstrappers in reverse order. */
  private async _revertInitialized(): Promise<void> {
    const reversed = [...this._initializedBootstrappers].reverse();
    for (const bootstrapper of reversed) {
      try {
        await bootstrapper.revert();
      } catch (error) {
        console.error(`Failed to revert bootstrapper "${bootstrapper.name}":`, error);
      }
    }
  }
}
