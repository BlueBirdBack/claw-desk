# ClawDesk

**Multi-tenant AI customer support platform built on OpenClaw.**

Enterprises get isolated AI support agents with real-time human supervision, dynamic model routing, and per-tenant billing — powered by OpenClaw's native multi-agent system.

> 🏆 Built for [Microsoft AI Dev Days Hackathon 2026](https://developer.microsoft.com/en-us/reactor/events/26647/)

---

## Architecture

ClawDesk doesn't reinvent multi-tenancy — it builds on what OpenClaw already does.

**Key insight:** OpenClaw natively supports multiple agents, each with its own workspace, model config, skills, and identity. ClawDesk's control plane maps tenants to OpenClaw agents.

```
┌─────────────────────────────────────────────────────┐
│              CLAWDESK CONTROL PLANE                  │
│                                                      │
│  ┌───────────┐ ┌──────────┐ ┌─────────────────────┐ │
│  │  Tenant    │ │   Auth   │ │   Billing/Metering  │ │
│  │  Manager   │ │ (Entra)  │ │   (Stripe)          │ │
│  └─────┬─────┘ └──────────┘ └─────────────────────┘ │
│        │                                              │
│  ┌─────┴────────────────────────────────────────────┐ │
│  │           Tenant Resolver                         │ │
│  │  header / subdomain / JWT → tenant ID → agent ID  │ │
│  └─────┬────────────────────────────────────────────┘ │
│        │                                              │
│  ┌─────┴────────────────────────────────────────────┐ │
│  │        Supervisor Dashboard                       │ │
│  │  real-time conversations · 🟢 🟡 🔴               │ │
│  └──────────────────────────────────────────────────┘ │
└──────────────────┬──────────────────────────────────┘
                   │ config.patch / chat.send / sessions.list
                   │ (OpenClaw WebSocket API)
                   ▼
┌──────────────────────────────────────────────────────┐
│              OPENCLAW GATEWAY                         │
│                                                       │
│  agents:                                              │
│    list:                                              │
│      - id: tenant-acme     ← Acme Corp agent         │
│        workspace: /data/tenants/tenant-acme           │
│        model: azure/gpt-4o                            │
│                                                       │
│      - id: tenant-globex   ← Globex Corp agent       │
│        workspace: /data/tenants/tenant-globex         │
│        model: azure/gpt-4o-mini                       │
│                                                       │
│      - id: tenant-initech  ← Initech agent           │
│        workspace: /data/tenants/tenant-initech        │
│        model: { primary: azure/gpt-4o,                │
│                 fallbacks: [azure/gpt-4o-mini] }      │
│                                                       │
│  Each agent has:                                      │
│    ✓ Own workspace (SOUL.md, knowledge base)          │
│    ✓ Own model config (with fallbacks)                │
│    ✓ Own identity (name, avatar)                      │
│    ✓ Own sandbox isolation                            │
│    ✓ Own session history                              │
└───────────────────────────┬──────────────────────────┘
                            │
                  ┌─────────┴─────────┐
                  │ Shared Infra       │
                  │ Azure OpenAI       │
                  │ Azure AI Search    │
                  │ Cosmos DB          │
                  └───────────────────┘
```

## Key Design Principle

> **Each tenant = one OpenClaw agent.**
>
> The control plane manages the mapping. OpenClaw handles everything else:
> model routing, conversation state, session management, and execution.
> No Docker containers. No child processes. Just config.

## How It Works

### Tenant Provisioning

```
Admin creates tenant → ClawDesk generates OpenClaw agent config →
  config.patch adds agent to agents.list[] → OpenClaw hot-reloads →
  Agent is live with its own workspace, model, and identity
```

### Customer Message Flow

```
Customer sends message → ClawDesk resolves tenant (API key/subdomain/JWT) →
  Maps to OpenClaw agent ID → chat.send to agent session →
  OpenClaw processes with tenant's model/workspace → Response flows back
```

### Supervisor Dashboard

```
Dashboard connects via WebSocket → sessions.list for all tenant agents →
  chat.history for conversation details → Real-time updates via polling/WS
```

## Core Patterns

### Bootstrapper Chain (inspired by [stancl/tenancy](https://github.com/archtechx/tenancy))

When a request arrives for a tenant, a chain of bootstrappers configures the context:

```typescript
interface TenancyBootstrapper {
  bootstrap(tenant: Tenant): Promise<void>;
  revert(): Promise<void>;
}

// Chain executes in order, reverts in reverse order
const bootstrappers = [
  AgentResolverBootstrapper,     // map tenant → OpenClaw agent ID
  ModelConfigBootstrapper,       // ensure tenant model config is current
  KnowledgeBaseBootstrapper,     // connect to tenant's RAG index
  MeteringBootstrapper,          // start usage tracking
];
```

### Tenant ↔ Agent Mapping

```typescript
// ClawDesk creates agent configs from tenant settings
const agentConfig = {
  id: `tenant-${tenant.slug}`,        // "tenant-acme"
  workspace: `/data/tenants/${slug}`,  // isolated workspace
  model: tenant.config.modelRouting.primary,
  sandbox: { mode: 'all', workspaceAccess: 'rw' },
};

// Patched into OpenClaw config dynamically
await gateway.patchConfig({
  agents: { list: [...existingAgents, agentConfig] }
}, baseHash);
```

### OpenClaw Gateway Client

```typescript
// ClawDesk talks to OpenClaw via its native WebSocket API
const gateway = new GatewayClient({
  url: 'ws://localhost:3001',
  token: process.env.OPENCLAW_TOKEN,
});

// Send customer message to tenant's agent
await gateway.chatSend({
  sessionKey: `agent:tenant-acme:customer-${customerId}`,
  message: customerMessage,
});

// Read conversation history for supervisor dashboard
const history = await gateway.chatHistory(sessionKey);
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| Control Plane API | Fastify + TypeScript |
| Dashboard | Next.js + React |
| Tenant Runtime | **OpenClaw** (native multi-agent) |
| Model Routing | Azure OpenAI (per-agent config) |
| Knowledge Base | Azure AI Search + Blob Storage |
| Auth | Azure Entra ID (OIDC) |
| Billing | Stripe (usage-based) |
| Real-time | WebSocket (supervisor dashboard) |
| Database | PostgreSQL (tenant metadata) |

## Features

- **Multi-tenant provisioning** — create isolated AI agents per customer via API
- **Zero-container tenancy** — each tenant is an OpenClaw agent, not a Docker container
- **Dynamic model routing** — per-tenant model config with fallbacks
- **Confidence gating** — AI drafts response, holds for supervisor if below threshold
- **Warm handoff** — AI→human with full conversation context + reasoning
- **Supervisor dashboard** — real-time view across all tenant conversations
- **Per-tenant knowledge base** — upload company docs for RAG
- **Usage-based billing** — per-conversation, per-token metering via Stripe
- **Hot provisioning** — new tenants go live in seconds (config reload, no restart)

## Project Structure

```
claw-desk/
├── packages/
│   ├── control-plane/         # Fastify API server
│   │   └── src/
│   │       ├── tenancy/           # Bootstrapper chain + context manager
│   │       ├── openclaw/          # Gateway client + tenant provisioner
│   │       ├── routing/           # Tenant resolver (header/subdomain/JWT)
│   │       ├── billing/           # Stripe metering
│   │       └── auth/              # Azure Entra ID OIDC
│   ├── dashboard/             # Next.js supervisor + admin UI
│   └── shared/                # Types, contracts, tenant schema
├── scripts/
│   └── demo-seed.ts           # Seed sample tenants
└── turbo.json
```

## Development

```bash
# Prerequisites
node >= 22, pnpm >= 9, OpenClaw running locally

# Install
pnpm install

# Dev (control plane + dashboard)
pnpm dev

# Test (bootstrapper chain, provisioner, resolver)
pnpm test

# OpenClaw must be running for integration tests
openclaw gateway start
```

## Why This Architecture?

| Approach | Tenants | Overhead | Provisioning | Complexity |
|----------|---------|----------|--------------|------------|
| Docker per tenant | Isolated containers | High (RAM per container) | Slow (pull + start) | High |
| Process per tenant | Child processes | Medium | Medium | Medium |
| **OpenClaw agents** | **Config entries** | **Near zero** | **Instant (hot reload)** | **Low** |

OpenClaw already solved multi-agent isolation. ClawDesk just adds the business layer: tenant CRUD, billing, supervisor UI, and customer-facing APIs.

## Hackathon Target

- 🏆 **Grand Prize**: Build AI Applications & Agents
- 🏢 **Best Enterprise Solution** ← strongest fit
- 🤝 **Best Multi-Agent System**
- ☁️ **Best Azure Integration**

## License

MIT

## Author

[BlueBirdBack](https://github.com/BlueBirdBack)
