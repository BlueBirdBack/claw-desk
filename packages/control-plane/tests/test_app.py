"""Tests for the FastAPI app — tenant CRUD + message routing."""

from __future__ import annotations

import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from clawdesk.app import create_app, _tenants, _api_keys
from clawdesk.models import OpenClawConfigSnapshot


# ─── Mock Gateway ─────────────────────────────────────────


@dataclass
class MockGateway:
    _config: dict[str, Any] = field(default_factory=lambda: {"agents": {"list": []}})
    _hash: str = "hash-1"
    _connected: bool = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def get_config(self) -> OpenClawConfigSnapshot:
        return OpenClawConfigSnapshot(config=deepcopy(self._config), hash=self._hash)

    async def patch_config(self, patch: dict, base_hash: str) -> None:
        if base_hash != self._hash:
            raise ValueError("hash mismatch")
        if "agents" in patch:
            self._config["agents"] = patch["agents"]
        self._hash = f"hash-{id(patch)}"

    @property
    def agent_list(self) -> list[dict]:
        return self._config.get("agents", {}).get("list", [])


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def tmp_workspace():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_gateway():
    return MockGateway()


@pytest.fixture
def app(tmp_workspace, mock_gateway):
    """Create app with mocked gateway."""
    import clawdesk.app as app_module

    application = create_app(
        gateway_url="ws://mock:3001",
        gateway_token="test-token",
        workspace_base_dir=tmp_workspace,
    )

    # Inject mock gateway + provisioner
    from clawdesk.openclaw.provisioner import TenantProvisioner, ProvisionerOptions

    app_module._gateway = mock_gateway
    app_module._provisioner = TenantProvisioner(
        ProvisionerOptions(workspace_base_dir=tmp_workspace, gateway=mock_gateway)
    )

    # Reset stores
    _tenants.clear()
    _api_keys.clear()

    yield application

    # Cleanup
    _tenants.clear()
    _api_keys.clear()


@pytest.fixture
def client(app):
    return TestClient(app)


# ─── Health ───────────────────────────────────────────────


class TestHealth:
    def test_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["tenant_count"] == 0


# ─── Create Tenant ────────────────────────────────────────


class TestCreateTenant:
    def test_creates_tenant(self, client, mock_gateway):
        resp = client.post("/tenants", json={
            "name": "Acme Corp",
            "slug": "acme",
            "primary_model": "azure/gpt-4o",
        })

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Acme Corp"
        assert data["slug"] == "acme"
        assert data["status"] == "active"
        assert data["openclaw_agent_id"] == "tenant-acme"
        assert data["api_key"] is not None
        assert data["api_key"].startswith("sk-")

        # Agent was added to OpenClaw config
        assert len(mock_gateway.agent_list) == 1
        assert mock_gateway.agent_list[0]["id"] == "tenant-acme"

    def test_creates_tenant_with_vision_model(self, client):
        resp = client.post("/tenants", json={
            "name": "Vision Corp",
            "slug": "vision",
            "primary_model": "openai-codex/gpt-5.3-codex-spark",
            "vision_model": "openai-codex/gpt-5.3-codex",
        })

        assert resp.status_code == 201
        data = resp.json()
        routing = data["config"]["model_routing"]
        assert routing["primary"] == "openai-codex/gpt-5.3-codex-spark"
        assert routing["vision_model"] == "openai-codex/gpt-5.3-codex"

    def test_rejects_duplicate_slug(self, client):
        client.post("/tenants", json={"name": "First", "slug": "acme"})
        resp = client.post("/tenants", json={"name": "Second", "slug": "acme"})

        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]


# ─── List Tenants ─────────────────────────────────────────


class TestListTenants:
    def test_returns_empty_list(self, client):
        resp = client.get("/tenants")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_created_tenants(self, client):
        client.post("/tenants", json={"name": "A", "slug": "a"})
        client.post("/tenants", json={"name": "B", "slug": "b"})

        resp = client.get("/tenants")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        slugs = {t["slug"] for t in data}
        assert slugs == {"a", "b"}


# ─── Get Tenant ───────────────────────────────────────────


class TestGetTenant:
    def test_returns_tenant(self, client):
        create_resp = client.post("/tenants", json={"name": "Acme", "slug": "acme"})
        tenant_id = create_resp.json()["id"]

        resp = client.get(f"/tenants/{tenant_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Acme"

    def test_returns_404_for_missing(self, client):
        resp = client.get("/tenants/nonexistent")
        assert resp.status_code == 404


# ─── Update Tenant ────────────────────────────────────────


class TestUpdateTenant:
    def test_updates_name(self, client):
        create_resp = client.post("/tenants", json={"name": "Old", "slug": "test"})
        tenant_id = create_resp.json()["id"]

        resp = client.patch(f"/tenants/{tenant_id}", json={"name": "New"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"

    def test_updates_model_routing(self, client):
        create_resp = client.post("/tenants", json={
            "name": "Test",
            "slug": "test",
            "primary_model": "azure/gpt-4o",
        })
        tenant_id = create_resp.json()["id"]

        resp = client.patch(f"/tenants/{tenant_id}", json={
            "vision_model": "azure/gpt-4o-vision",
        })
        assert resp.status_code == 200
        assert resp.json()["config"]["model_routing"]["vision_model"] == "azure/gpt-4o-vision"

    def test_returns_404_for_missing(self, client):
        resp = client.patch("/tenants/nonexistent", json={"name": "X"})
        assert resp.status_code == 404


# ─── Delete Tenant ────────────────────────────────────────


class TestDeleteTenant:
    def test_deletes_tenant(self, client, mock_gateway):
        create_resp = client.post("/tenants", json={"name": "Acme", "slug": "acme"})
        tenant_id = create_resp.json()["id"]

        resp = client.delete(f"/tenants/{tenant_id}")
        assert resp.status_code == 204

        # Gone
        assert client.get(f"/tenants/{tenant_id}").status_code == 404

        # Agent removed from OpenClaw
        assert len(mock_gateway.agent_list) == 0

    def test_returns_404_for_missing(self, client):
        resp = client.delete("/tenants/nonexistent")
        assert resp.status_code == 404


# ─── Message Routing ─────────────────────────────────────


class TestMessageRouting:
    def test_routes_text_to_primary_model(self, client):
        create_resp = client.post("/tenants", json={
            "name": "Test",
            "slug": "test",
            "primary_model": "openai-codex/gpt-5.3-codex-spark",
            "vision_model": "openai-codex/gpt-5.3-codex",
        })
        tenant_id = create_resp.json()["id"]

        resp = client.post(f"/tenants/{tenant_id}/messages", json={
            "customer_id": "cust-1",
            "message": "How do I reset my password?",
            "has_images": False,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["model_used"] == "openai-codex/gpt-5.3-codex-spark"
        assert data["routing_reason"] == "default: text-only"

    def test_routes_images_to_vision_model(self, client):
        create_resp = client.post("/tenants", json={
            "name": "Test",
            "slug": "test",
            "primary_model": "openai-codex/gpt-5.3-codex-spark",
            "vision_model": "openai-codex/gpt-5.3-codex",
        })
        tenant_id = create_resp.json()["id"]

        resp = client.post(f"/tenants/{tenant_id}/messages", json={
            "customer_id": "cust-1",
            "message": "What's in this screenshot?",
            "has_images": True,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["model_used"] == "openai-codex/gpt-5.3-codex"
        assert "vision" in data["routing_reason"]

    def test_routes_long_context_to_long_context_model(self, client):
        create_resp = client.post("/tenants", json={
            "name": "Test",
            "slug": "test",
            "primary_model": "openai-codex/gpt-5.3-codex-spark",
            "long_context_model": "openai-codex/gpt-5.3-codex",
        })
        tenant_id = create_resp.json()["id"]

        resp = client.post(f"/tenants/{tenant_id}/messages", json={
            "customer_id": "cust-1",
            "message": "Analyze this long document...",
            "estimated_tokens": 150_000,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["model_used"] == "openai-codex/gpt-5.3-codex"
        assert "long-context" in data["routing_reason"]

    def test_returns_session_key(self, client):
        create_resp = client.post("/tenants", json={"name": "Test", "slug": "test"})
        tenant_id = create_resp.json()["id"]

        resp = client.post(f"/tenants/{tenant_id}/messages", json={
            "customer_id": "cust-42",
            "message": "hello",
        })

        data = resp.json()
        assert data["session_key"] == "agent:tenant-test:customer-cust-42"

    def test_rejects_inactive_tenant(self, client):
        create_resp = client.post("/tenants", json={"name": "Test", "slug": "test"})
        tenant_id = create_resp.json()["id"]

        # Pause the tenant
        client.patch(f"/tenants/{tenant_id}", json={"status": "paused"})

        resp = client.post(f"/tenants/{tenant_id}/messages", json={
            "customer_id": "cust-1",
            "message": "hello",
        })
        assert resp.status_code == 403

    def test_returns_404_for_missing_tenant(self, client):
        resp = client.post("/tenants/nonexistent/messages", json={
            "customer_id": "cust-1",
            "message": "hello",
        })
        assert resp.status_code == 404


# ─── API Key Auth ─────────────────────────────────────────


class TestChatViaApiKey:
    def test_routes_via_api_key(self, client):
        create_resp = client.post("/tenants", json={
            "name": "Acme",
            "slug": "acme",
            "primary_model": "openai-codex/gpt-5.3-codex-spark",
            "vision_model": "openai-codex/gpt-5.3-codex",
        })
        api_key = create_resp.json()["api_key"]

        resp = client.post(
            "/chat",
            json={"customer_id": "cust-1", "message": "Help!", "has_images": True},
            headers={"x-api-key": api_key},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["model_used"] == "openai-codex/gpt-5.3-codex"
        assert "vision" in data["routing_reason"]

    def test_rejects_invalid_api_key(self, client):
        resp = client.post(
            "/chat",
            json={"customer_id": "cust-1", "message": "hello"},
            headers={"x-api-key": "sk-invalid"},
        )
        assert resp.status_code == 401
