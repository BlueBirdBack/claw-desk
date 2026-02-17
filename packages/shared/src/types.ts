/**
 * Core type definitions for ClawDesk multi-tenancy.
 */

// ─── Tenant ──────────────────────────────────────────────

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  status: TenantStatus;
  config: TenantConfig;
  billing: TenantBilling;
  createdAt: Date;
  updatedAt: Date;
}

export type TenantStatus = 'provisioning' | 'active' | 'paused' | 'suspended' | 'deleting';

export interface TenantConfig {
  /** Model routing rules */
  modelRouting: ModelRoutingConfig;
  /** Confidence threshold (0-1). Below this, hold for supervisor. */
  confidenceThreshold: number;
  /** Knowledge base ID (Azure AI Search index) */
  knowledgeBaseId?: string;
  /** After-hours autonomy: raise threshold when no supervisors online */
  afterHoursThreshold?: number;
}

export interface ModelRoutingConfig {
  /** Model for simple text queries (fast, cheap) */
  text: string;
  /** Model for image/multimodal inputs */
  vision: string;
  /** Model for complex reasoning */
  reasoning: string;
  /** Sentiment score below which to escalate to human */
  escalationSentiment: number;
}

export interface TenantBilling {
  stripeCustomerId?: string;
  stripeSubscriptionId?: string;
  plan: PlanId;
  usageThisMonth: UsageMetrics;
}

export type PlanId = 'starter' | 'pro' | 'enterprise';

export interface UsageMetrics {
  conversations: number;
  inputTokens: number;
  outputTokens: number;
  knowledgeBaseQueries: number;
}

// ─── Instance ────────────────────────────────────────────

export interface TenantInstance {
  tenantId: string;
  containerId?: string;
  endpoint?: string;
  status: InstanceStatus;
  lastActiveAt?: Date;
  pausedAt?: Date;
  /** Stored replica count before pause (for resume) */
  replicasBefore?: number;
}

export type InstanceStatus = 'creating' | 'running' | 'pausing' | 'paused' | 'resuming' | 'destroying' | 'destroyed';

// ─── Bootstrapper ────────────────────────────────────────

/**
 * TenancyBootstrapper — the core multi-tenancy abstraction.
 *
 * Inspired by stancl/tenancy for Laravel.
 * Each bootstrapper configures one subsystem for a tenant.
 * Chain executes in order, reverts in reverse order.
 */
export interface TenancyBootstrapper {
  /** Human-readable name for logging/debugging */
  readonly name: string;

  /** Configure this subsystem for the given tenant */
  bootstrap(tenant: Tenant): Promise<void>;

  /** Revert this subsystem to central/default context */
  revert(): Promise<void>;
}

// ─── Tenant Resolution ───────────────────────────────────

export interface TenantResolver {
  /** Human-readable name */
  readonly name: string;

  /**
   * Attempt to resolve a tenant ID from the request context.
   * Returns null if this resolver can't identify the tenant.
   */
  resolve(context: RequestContext): Promise<string | null>;
}

export interface RequestContext {
  headers: Record<string, string | undefined>;
  hostname?: string;
  path?: string;
  query?: Record<string, string>;
  /** JWT claims (if authenticated) */
  claims?: Record<string, unknown>;
}

// ─── Conversation ────────────────────────────────────────

export interface Conversation {
  id: string;
  tenantId: string;
  customerId: string;
  status: ConversationStatus;
  assignedTo: 'ai' | 'human';
  confidence: number;
  messages: ConversationMessage[];
  startedAt: Date;
  lastMessageAt: Date;
}

export type ConversationStatus = 'active' | 'waiting_approval' | 'escalated' | 'resolved';

export interface ConversationMessage {
  id: string;
  role: 'customer' | 'ai' | 'supervisor';
  content: string;
  timestamp: Date;
  metadata?: {
    model?: string;
    confidence?: number;
    tokensUsed?: number;
  };
}
