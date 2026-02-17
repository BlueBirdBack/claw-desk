"""
Core models for ClawDesk multi-tenancy.

Each tenant maps to an OpenClaw agent entry.
No Docker, no child processes — just config management + API routing.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─── Tenant ──────────────────────────────────────────────


class TenantStatus(str, Enum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    PAUSED = "paused"
    SUSPENDED = "suspended"
    DELETING = "deleting"


class PlanId(str, Enum):
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class UsageMetrics(BaseModel):
    conversations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    knowledge_base_queries: int = 0


class ModelRoutingConfig(BaseModel):
    """Per-tenant model routing rules."""

    primary: str
    fallbacks: list[str] = Field(default_factory=list)
    escalation_sentiment: float = -0.5

    # Smart routing rules
    vision_model: str | None = None  # Override model when images detected
    long_context_model: str | None = None  # Override for large contexts
    long_context_threshold: int = 100_000  # Token count to trigger


class TenantConfig(BaseModel):
    model_routing: ModelRoutingConfig
    confidence_threshold: float = 0.7
    knowledge_base_id: str | None = None
    system_prompt: str | None = None
    after_hours_threshold: float | None = None


class TenantBilling(BaseModel):
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    plan: PlanId = PlanId.STARTER
    usage_this_month: UsageMetrics = Field(default_factory=UsageMetrics)


class Tenant(BaseModel):
    id: str
    name: str
    slug: str
    status: TenantStatus = TenantStatus.PROVISIONING
    config: TenantConfig
    billing: TenantBilling = Field(default_factory=TenantBilling)
    openclaw_agent_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ─── OpenClaw Agent Mapping ──────────────────────────────


class OpenClawAgentConfig(BaseModel):
    """Mirrors an OpenClaw agents.list[] entry."""

    id: str
    name: str | None = None
    workspace: str | None = None
    model: str | dict[str, Any] | None = None
    skills: list[str] | None = None
    sandbox: dict[str, Any] | None = None
    identity: dict[str, str] | None = None


class OpenClawAgentMapping(BaseModel):
    tenant_id: str
    agent_id: str
    workspace_path: str
    agent_config: OpenClawAgentConfig


# ─── Tenant Resolution ───────────────────────────────────


class RequestContext(BaseModel):
    headers: dict[str, str | None] = Field(default_factory=dict)
    hostname: str | None = None
    path: str | None = None
    query: dict[str, str] = Field(default_factory=dict)
    claims: dict[str, Any] = Field(default_factory=dict)


# ─── OpenClaw Gateway ────────────────────────────────────


class OpenClawConfigSnapshot(BaseModel):
    config: dict[str, Any]
    hash: str


class ChatSendParams(BaseModel):
    session_key: str
    message: str
    agent_id: str | None = None
    attachments: list[dict[str, str]] = Field(default_factory=list)


class ChatSendResult(BaseModel):
    ok: bool
    message_id: str | None = None
    response: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: str | None = None
    metadata: dict[str, Any] | None = None


class SessionEntry(BaseModel):
    key: str
    agent_id: str
    last_activity: str | None = None
    message_count: int | None = None


# ─── Conversation ─────────────────────────────────────────


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    WAITING_APPROVAL = "waiting_approval"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


class Conversation(BaseModel):
    id: str
    tenant_id: str
    customer_id: str
    openclaw_session_key: str
    status: ConversationStatus = ConversationStatus.ACTIVE
    assigned_to: str = "ai"
    confidence: float = 1.0
    started_at: datetime = Field(default_factory=datetime.now)
    last_message_at: datetime = Field(default_factory=datetime.now)


# ─── Provisioning Results ────────────────────────────────


class TenantProvisioningResult(BaseModel):
    tenant_id: str
    agent_id: str
    workspace_path: str
    status: str  # "success" | "failed"
    error: str | None = None


class TenantDeprovisioningResult(BaseModel):
    tenant_id: str
    status: str  # "success" | "failed"
    error: str | None = None


# ─── Model Routing ────────────────────────────────────────


class MessageAnalysis(BaseModel):
    """Result of analyzing an inbound message for routing."""

    has_images: bool = False
    estimated_tokens: int = 0
    sentiment_score: float = 0.0  # -1.0 (angry) to 1.0 (happy)


def pick_model(config: ModelRoutingConfig, analysis: MessageAnalysis) -> str:
    """
    Smart model routing based on message content.

    Priority:
    1. Vision model if images detected and vision_model configured
    2. Long context model if token count exceeds threshold
    3. Primary model (default)
    """
    if analysis.has_images and config.vision_model:
        return config.vision_model

    if (
        analysis.estimated_tokens > config.long_context_threshold
        and config.long_context_model
    ):
        return config.long_context_model

    return config.primary
