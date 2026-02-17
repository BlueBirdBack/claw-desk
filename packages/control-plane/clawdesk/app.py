"""
ClawDesk — FastAPI application.

Tenant CRUD + message routing + smart model selection.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, Field

from clawdesk.models import (
    Tenant,
    TenantConfig,
    TenantStatus,
    ModelRoutingConfig,
    MessageAnalysis,
    pick_model,
)
from clawdesk.openclaw.client import GatewayClient, GatewayClientOptions
from clawdesk.openclaw.provisioner import TenantProvisioner, ProvisionerOptions
from clawdesk.tenancy.resolver import (
    HeaderTenantResolver,
    TenantResolverChain,
)
from clawdesk.models import RequestContext

# ─── In-memory store (swap for DB later) ─────────────────

_tenants: dict[str, Tenant] = {}
_api_keys: dict[str, str] = {}  # api_key → tenant_id

# ─── App state ────────────────────────────────────────────

_gateway: GatewayClient | None = None
_provisioner: TenantProvisioner | None = None
_resolver: TenantResolverChain | None = None


def get_gateway() -> GatewayClient:
    if _gateway is None:
        raise RuntimeError("Gateway client not initialized")
    return _gateway


def get_provisioner() -> TenantProvisioner:
    if _provisioner is None:
        raise RuntimeError("Provisioner not initialized")
    return _provisioner


# ─── Request/Response models ─────────────────────────────


class CreateTenantRequest(BaseModel):
    name: str
    slug: str
    system_prompt: str | None = None
    primary_model: str = "azure/gpt-4o"
    fallback_models: list[str] = Field(default_factory=list)
    vision_model: str | None = None
    long_context_model: str | None = None
    confidence_threshold: float = 0.7


class UpdateTenantRequest(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    primary_model: str | None = None
    vision_model: str | None = None
    long_context_model: str | None = None
    confidence_threshold: float | None = None
    status: TenantStatus | None = None


class SendMessageRequest(BaseModel):
    customer_id: str
    message: str
    has_images: bool = False
    estimated_tokens: int = 0


class SendMessageResponse(BaseModel):
    tenant_id: str
    customer_id: str
    model_used: str
    routing_reason: str
    session_key: str


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    status: TenantStatus
    openclaw_agent_id: str
    api_key: str | None = None
    config: TenantConfig


class HealthResponse(BaseModel):
    status: str
    gateway_connected: bool
    tenant_count: int


# ─── App setup ────────────────────────────────────────────


def create_app(
    gateway_url: str = "ws://localhost:3001",
    gateway_token: str = "",
    workspace_base_dir: str = "/data/tenants",
) -> FastAPI:
    """Create the FastAPI app with dependency injection."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _gateway, _provisioner, _resolver

        _gateway = GatewayClient(
            GatewayClientOptions(url=gateway_url, token=gateway_token)
        )
        _provisioner = TenantProvisioner(
            ProvisionerOptions(workspace_base_dir=workspace_base_dir, gateway=_gateway)
        )

        # Resolver chain: API key header → tenant ID
        async def lookup_api_key(key: str) -> str | None:
            return _api_keys.get(key)

        _resolver = TenantResolverChain([
            HeaderTenantResolver("x-api-key", lookup_api_key),
        ])

        yield

        if _gateway:
            await _gateway.disconnect()

    app = FastAPI(
        title="ClawDesk",
        description="Multi-tenant AI customer support platform built on OpenClaw",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ─── Routes ───────────────────────────────────────

    @app.get("/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            status="ok",
            gateway_connected=_gateway.connected if _gateway else False,
            tenant_count=len(_tenants),
        )

    # ─── Tenant CRUD ──────────────────────────────────

    @app.post("/tenants", response_model=TenantResponse, status_code=201)
    async def create_tenant(req: CreateTenantRequest):
        provisioner = get_provisioner()

        # Check slug uniqueness
        if any(t.slug == req.slug for t in _tenants.values()):
            raise HTTPException(400, f"Tenant with slug '{req.slug}' already exists")

        # Build tenant
        import uuid

        tenant_id = str(uuid.uuid4())[:8]
        api_key = f"sk-{uuid.uuid4().hex[:24]}"

        tenant = Tenant(
            id=tenant_id,
            name=req.name,
            slug=req.slug,
            status=TenantStatus.PROVISIONING,
            openclaw_agent_id=provisioner.tenant_to_agent_id(req.slug),
            config=TenantConfig(
                model_routing=ModelRoutingConfig(
                    primary=req.primary_model,
                    fallbacks=req.fallback_models,
                    vision_model=req.vision_model,
                    long_context_model=req.long_context_model,
                ),
                confidence_threshold=req.confidence_threshold,
                system_prompt=req.system_prompt,
            ),
        )

        # Provision in OpenClaw
        result = await provisioner.provision(tenant)

        if result.status == "failed":
            raise HTTPException(500, f"Provisioning failed: {result.error}")

        tenant.status = TenantStatus.ACTIVE
        _tenants[tenant_id] = tenant
        _api_keys[api_key] = tenant_id

        return TenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            status=tenant.status,
            openclaw_agent_id=tenant.openclaw_agent_id,
            api_key=api_key,
            config=tenant.config,
        )

    @app.get("/tenants", response_model=list[TenantResponse])
    async def list_tenants():
        return [
            TenantResponse(
                id=t.id,
                name=t.name,
                slug=t.slug,
                status=t.status,
                openclaw_agent_id=t.openclaw_agent_id,
                config=t.config,
            )
            for t in _tenants.values()
        ]

    @app.get("/tenants/{tenant_id}", response_model=TenantResponse)
    async def get_tenant(tenant_id: str):
        tenant = _tenants.get(tenant_id)
        if not tenant:
            raise HTTPException(404, "Tenant not found")
        return TenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            status=tenant.status,
            openclaw_agent_id=tenant.openclaw_agent_id,
            config=tenant.config,
        )

    @app.patch("/tenants/{tenant_id}", response_model=TenantResponse)
    async def update_tenant(tenant_id: str, req: UpdateTenantRequest):
        tenant = _tenants.get(tenant_id)
        if not tenant:
            raise HTTPException(404, "Tenant not found")

        if req.name is not None:
            tenant.name = req.name
        if req.system_prompt is not None:
            tenant.config.system_prompt = req.system_prompt
        if req.primary_model is not None:
            tenant.config.model_routing.primary = req.primary_model
        if req.vision_model is not None:
            tenant.config.model_routing.vision_model = req.vision_model
        if req.long_context_model is not None:
            tenant.config.model_routing.long_context_model = req.long_context_model
        if req.confidence_threshold is not None:
            tenant.config.confidence_threshold = req.confidence_threshold
        if req.status is not None:
            tenant.status = req.status

        from datetime import datetime

        tenant.updated_at = datetime.now()

        return TenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            status=tenant.status,
            openclaw_agent_id=tenant.openclaw_agent_id,
            config=tenant.config,
        )

    @app.delete("/tenants/{tenant_id}", status_code=204)
    async def delete_tenant(tenant_id: str):
        tenant = _tenants.get(tenant_id)
        if not tenant:
            raise HTTPException(404, "Tenant not found")

        provisioner = get_provisioner()
        result = await provisioner.deprovision(tenant)

        if result.status == "failed":
            raise HTTPException(500, f"Deprovisioning failed: {result.error}")

        # Remove API keys for this tenant
        keys_to_remove = [k for k, v in _api_keys.items() if v == tenant_id]
        for k in keys_to_remove:
            del _api_keys[k]

        del _tenants[tenant_id]

    # ─── Message Routing ──────────────────────────────

    @app.post("/tenants/{tenant_id}/messages", response_model=SendMessageResponse)
    async def send_message(tenant_id: str, req: SendMessageRequest):
        """
        Send a customer message to a tenant's AI agent.
        Smart model routing: picks the right model based on message content.
        """
        tenant = _tenants.get(tenant_id)
        if not tenant:
            raise HTTPException(404, "Tenant not found")

        if tenant.status != TenantStatus.ACTIVE:
            raise HTTPException(
                403, f"Tenant is {tenant.status.value}, not accepting messages"
            )

        # Smart model routing
        analysis = MessageAnalysis(
            has_images=req.has_images,
            estimated_tokens=req.estimated_tokens,
        )
        selected_model = pick_model(tenant.config.model_routing, analysis)

        # Determine routing reason for transparency
        if req.has_images and tenant.config.model_routing.vision_model:
            reason = "vision: images detected"
        elif (
            req.estimated_tokens > tenant.config.model_routing.long_context_threshold
            and tenant.config.model_routing.long_context_model
        ):
            reason = f"long-context: {req.estimated_tokens} tokens > {tenant.config.model_routing.long_context_threshold} threshold"
        else:
            reason = "default: text-only"

        # Build session key: agent:<agent-id>:customer-<customer-id>
        session_key = (
            f"agent:{tenant.openclaw_agent_id}:customer-{req.customer_id}"
        )

        # TODO: actually send via gateway.chat_send() when connected
        # For now, return the routing decision (demo-friendly)

        return SendMessageResponse(
            tenant_id=tenant.id,
            customer_id=req.customer_id,
            model_used=selected_model,
            routing_reason=reason,
            session_key=session_key,
        )

    # ─── API Key auth (customer-facing) ───────────────

    @app.post("/chat")
    async def chat_via_api_key(
        req: SendMessageRequest,
        x_api_key: str = Header(...),
    ):
        """
        Customer-facing endpoint. Resolves tenant from API key,
        then routes message to the right agent.
        """
        tenant_id = _api_keys.get(x_api_key)
        if not tenant_id:
            raise HTTPException(401, "Invalid API key")

        tenant = _tenants.get(tenant_id)
        if not tenant:
            raise HTTPException(404, "Tenant not found")

        # Reuse the same routing logic
        analysis = MessageAnalysis(
            has_images=req.has_images,
            estimated_tokens=req.estimated_tokens,
        )
        selected_model = pick_model(tenant.config.model_routing, analysis)

        reason = "default: text-only"
        if req.has_images and tenant.config.model_routing.vision_model:
            reason = "vision: images detected"
        elif (
            req.estimated_tokens > tenant.config.model_routing.long_context_threshold
            and tenant.config.model_routing.long_context_model
        ):
            reason = f"long-context: {req.estimated_tokens} tokens"

        session_key = f"agent:{tenant.openclaw_agent_id}:customer-{req.customer_id}"

        return SendMessageResponse(
            tenant_id=tenant.id,
            customer_id=req.customer_id,
            model_used=selected_model,
            routing_reason=reason,
            session_key=session_key,
        )

    return app


# ─── Entry point ──────────────────────────────────────────


def main():
    import os
    import uvicorn

    app = create_app(
        gateway_url=os.getenv("OPENCLAW_GATEWAY_URL", "ws://localhost:3001"),
        gateway_token=os.getenv("OPENCLAW_GATEWAY_TOKEN", ""),
        workspace_base_dir=os.getenv("CLAWDESK_WORKSPACE_DIR", "/data/tenants"),
    )
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
