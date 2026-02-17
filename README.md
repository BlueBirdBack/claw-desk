# ClawDesk

**Multi-tenant AI customer support platform built on OpenClaw.**

Enterprises get isolated AI support agents with real-time human supervision, dynamic model routing, and per-tenant billing.

> 🏆 Built for [Microsoft AI Dev Days Hackathon 2026](https://developer.microsoft.com/en-us/reactor/events/26647/)

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              CONTROL PLANE                   │
│                                              │
│  ┌───────────┐ ┌──────────┐ ┌─────────────┐ │
│  │  Tenant    │ │   Auth   │ │   Billing   │ │
│  │  Manager   │ │ (Entra)  │ │  (Stripe)   │ │
│  └─────┬─────┘ └──────────┘ └─────────────┘ │
│        │                                      │
│  ┌─────┴────────────────────────────────────┐ │
│  │         Tenant Router / Proxy             │ │
│  │  resolve tenant → bootstrap → route       │ │
│  └─────┬────────────────────────────────────┘ │
│        │                                      │
│  ┌─────┴────────────────────────────────────┐ │
│  │        Supervisor Dashboard               │ │
│  │  real-time conversations · 🟢 🟡 🔴       │ │
│  └──────────────────────────────────────────┘ │
└──────────────────┬──────────────────────────┘
                   │
     ┌─────────────┼─────────────┐
     │             │             │
 ┌───┴────┐  ┌───┴────┐  ┌───┴────┐
 │Tenant A │  │Tenant B │  │Tenant C │
 │OpenClaw │  │OpenClaw │  │OpenClaw │
 │Instance │  │Instance │  │Instance │
 └───┬────┘  └───┬────┘  └───┬────┘
     │           │           │
 ┌───┴───────────┴───────────┴────┐
 │      Shared Infrastructure      │
 │  Azure OpenAI · AI Search ·     │
 │  Cosmos DB · Web PubSub         │
 └────────────────────────────────┘
```

## Key Design Principle

> **OpenClaw instances don't know they're part of a multi-tenant platform.**
>
> The control plane handles tenant identification, context propagation,
> instance lifecycle, config sync, usage metering, and billing.
> OpenClaw just does what OpenClaw does.

## Core Patterns

### Bootstrapper Chain (inspired by [stancl/tenancy](https://github.com/archtechx/tenancy))

When a request arrives for a tenant, a chain of bootstrappers configures the entire context:

```typescript
interface TenancyBootstrapper {
  bootstrap(tenant: Tenant): Promise<void>;
  revert(): Promise<void>;
}

// Chain executes in order, reverts in reverse order
const bootstrappers = [
  InstanceRouterBootstrapper,    // find/wake OpenClaw instance
  ModelRouterBootstrapper,       // load tenant model config
  KnowledgeBaseBootstrapper,     // connect to tenant RAG
  ConversationBootstrapper,      // scope to tenant conversation store
  MeteringBootstrapper,          // start usage tracking
];
```

### Instance Lifecycle (inspired by [vCluster](https://github.com/loft-sh/vcluster))

```
create  → provision container → push config → ready
pause   → save state → scale to 0 → free RAM
resume  → restore state → scale to 1 → route traffic
destroy → drain connections → delete container → cleanup
```

### Sleep/Wake for Cost Efficiency

Idle tenants are paused (0 RAM). Incoming messages trigger automatic resume (~3-5s cold start). Typical active ratio: 20% → 5x capacity multiplier.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Control Plane API | Fastify + TypeScript |
| Dashboard | Next.js + React |
| Database | PostgreSQL (control plane) |
| Cache / PubSub | Redis |
| Tenant Instances | OpenClaw (Docker containers) |
| Model Routing | Azure OpenAI (multiple deployments) |
| Knowledge Base | Azure AI Search + Blob Storage |
| Auth | Azure Entra ID (OIDC) |
| Billing | Stripe (usage-based) |
| Real-time | WebSocket (supervisor dashboard) |

## Features

- **Multi-tenant provisioning** — create isolated AI support agents per customer
- **Dynamic model routing** — text→spark (fast), image→vision, complex→reasoning, angry→human
- **Confidence gating** — AI drafts response, holds for supervisor if confidence < threshold
- **Warm handoff** — AI→human with full conversation context + AI reasoning summary
- **Supervisor dashboard** — real-time view of all conversations across tenants
- **Per-tenant knowledge base** — upload company docs/FAQs for RAG
- **Usage-based billing** — per-conversation, per-model-token metering
- **Sleep/wake** — idle instances pause automatically, resume on demand

## Project Structure

```
claw-desk/
├── packages/
│   ├── control-plane/     # Fastify API server
│   │   └── src/
│   │       ├── tenancy/       # Bootstrapper chain + context manager
│   │       ├── instances/     # Container lifecycle (create/pause/resume/destroy)
│   │       ├── routing/       # Tenant resolver (header/subdomain/JWT)
│   │       ├── billing/       # Stripe metering + usage tracking
│   │       └── auth/          # Azure Entra ID OIDC
│   ├── dashboard/         # Next.js supervisor + admin UI
│   ├── shared/            # Types, contracts, tenant schema
│   └── tenant-stub/       # Lightweight OpenClaw simulator (for local dev)
├── docker/
│   ├── docker-compose.yml         # Full stack
│   └── docker-compose.local.yml   # Lightweight local dev
├── scripts/
│   └── demo-seed.ts       # Seed sample tenants + conversations
└── tests/
    └── tenancy/           # TDD: bootstrapper chain, resolver, context
```

## Development

```bash
# Prerequisites
node >= 22, pnpm >= 9, docker

# Install
pnpm install

# Dev (control plane + dashboard + 1 stub tenant)
pnpm dev

# Test (bootstrapper chain, tenant resolver, context manager)
pnpm test

# Full stack with real OpenClaw instances
docker compose up
```

## Hackathon Target

- 🏆 **Grand Prize**: Build AI Applications & Agents
- 🏢 **Best Enterprise Solution** ← strongest fit
- 🤝 **Best Multi-Agent System**
- ☁️ **Best Azure Integration**

## License

MIT

## Author

[BlueBirdBack](https://github.com/BlueBirdBack)
