/**
 * Core type definitions for ClawDesk multi-tenancy.
 *
 * Architecture: ClawDesk is a control plane that wraps OpenClaw's native
 * multi-agent system. Each tenant maps to an OpenClaw agent entry.
 * No Docker, no separate processes — just config management + API routing.
 */

// ─── Tenant ──────────────────────────────────────────────

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  status: TenantStatus;
  config: TenantConfig;
  billing: TenantBilling;
  /** The OpenClaw agent ID this tenant maps to */
  openclawAgentId: string;
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
  /** System prompt for the tenant's AI agent */
  systemPrompt?: string;
  /** After-hours autonomy: raise threshold when no supervisors online */
  afterHoursThreshold?: number;
}

export interface ModelRoutingConfig {
  /** Primary model for the tenant (provider/model format) */
  primary: string;
  /** Fallback models */
  fallbacks?: string[];
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

// ─── OpenClaw Agent Mapping ──────────────────────────────

/**
 * Maps a ClawDesk tenant to an OpenClaw agent config entry.
 * This is the bridge between our tenancy model and OpenClaw's native system.
 */
export interface OpenClawAgentMapping {
  /** ClawDesk tenant ID */
  tenantId: string;
  /** OpenClaw agent ID (e.g., "tenant-acme") */
  agentId: string;
  /** OpenClaw workspace path for this tenant */
  workspacePath: string;
  /** OpenClaw agent config fragment (what goes into agents.list[]) */
  agentConfig: OpenClawAgentConfig;
}

/**
 * OpenClaw agent config — mirrors the shape of agents.list[] entries.
 * We generate this from TenantConfig when provisioning.
 */
export interface OpenClawAgentConfig {
  id: string;
  name?: string;
  workspace?: string;
  model?: string | { primary?: string; fallbacks?: string[] };
  skills?: string[];
  sandbox?: {
    mode?: 'off' | 'non-main' | 'all';
    workspaceAccess?: 'none' | 'ro' | 'rw';
  };
  identity?: {
    name?: string;
    avatar?: string;
  };
}

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

// ─── OpenClaw Gateway Client ─────────────────────────────

/**
 * Interface for communicating with an OpenClaw gateway instance.
 * The control plane uses this to manage agents and route messages.
 */
export interface OpenClawGatewayClient {
  /**
   * Get current gateway config.
   * Maps to: config.get WS method
   */
  getConfig(): Promise<OpenClawConfigSnapshot>;

  /**
   * Patch gateway config (e.g., add/remove agent).
   * Maps to: config.patch WS method
   * Triggers config reload (hot for agent changes).
   */
  patchConfig(patch: Record<string, unknown>, baseHash: string): Promise<void>;

  /**
   * Send a message into a specific session.
   * Maps to: chat.send WS method
   */
  chatSend(params: ChatSendParams): Promise<ChatSendResult>;

  /**
   * Get conversation history for a session.
   * Maps to: chat.history WS method
   */
  chatHistory(sessionKey: string, limit?: number): Promise<ChatMessage[]>;

  /**
   * List active sessions.
   * Maps to: sessions.list WS method
   */
  sessionsList(params?: SessionsListParams): Promise<SessionEntry[]>;
}

export interface OpenClawConfigSnapshot {
  config: Record<string, unknown>;
  hash: string;
}

export interface ChatSendParams {
  sessionKey: string;
  message: string;
  agentId?: string;
  attachments?: Array<{ type: string; url: string }>;
}

export interface ChatSendResult {
  ok: boolean;
  messageId?: string;
  response?: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
  metadata?: Record<string, unknown>;
}

export interface SessionsListParams {
  agentId?: string;
  activeMinutes?: number;
  limit?: number;
}

export interface SessionEntry {
  key: string;
  agentId: string;
  lastActivity?: string;
  messageCount?: number;
}

// ─── Conversation (ClawDesk layer) ───────────────────────

export interface Conversation {
  id: string;
  tenantId: string;
  customerId: string;
  /** The OpenClaw session key for this conversation */
  openclawSessionKey: string;
  status: ConversationStatus;
  assignedTo: 'ai' | 'human';
  confidence: number;
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

// ─── Tenant Provisioning ─────────────────────────────────

/**
 * What happens when a tenant is created:
 * 1. Generate OpenClaw agent ID from tenant slug
 * 2. Build agent config from tenant config
 * 3. Create workspace directory with SOUL.md, AGENTS.md etc.
 * 4. Patch OpenClaw config to add agent to agents.list[]
 * 5. OpenClaw hot-reloads, agent becomes available
 * 6. Set up channel binding if needed
 */
export interface TenantProvisioningResult {
  tenantId: string;
  agentId: string;
  workspacePath: string;
  status: 'success' | 'failed';
  error?: string;
}

/**
 * What happens when a tenant is deleted:
 * 1. Remove agent from OpenClaw config via config.patch
 * 2. Archive workspace directory
 * 3. Clean up session data
 * 4. Remove channel bindings
 */
export interface TenantDeprovisioningResult {
  tenantId: string;
  status: 'success' | 'failed';
  error?: string;
}
