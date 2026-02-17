"""
OpenClaw Gateway Client

Communicates with an OpenClaw gateway instance via its WebSocket API.
Protocol: JSON-RPC-style — { id, method, params } → { id, result/error }.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import websockets
from websockets.asyncio.client import ClientConnection

from clawdesk.models import (
    ChatMessage,
    ChatSendParams,
    ChatSendResult,
    OpenClawConfigSnapshot,
    SessionEntry,
)


@dataclass
class GatewayClientOptions:
    url: str
    token: str
    connect_timeout: float = 5.0
    request_timeout: float = 30.0


class GatewayClient:
    def __init__(self, options: GatewayClientOptions) -> None:
        self._options = options
        self._ws: ClientConnection | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._reader_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def connect(self) -> None:
        if self._ws is not None:
            return

        self._ws = await asyncio.wait_for(
            websockets.connect(
                self._options.url,
                additional_headers={"Authorization": f"Bearer {self._options.token}"},
            ),
            timeout=self._options.connect_timeout,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def disconnect(self) -> None:
        if self._ws:
            if self._reader_task:
                self._reader_task.cancel()
                self._reader_task = None
            await self._ws.close()
            self._ws = None
            self._reject_all(Exception("WebSocket closed"))

    # ─── OpenClaw API Methods ────────────────────────────

    async def get_config(self) -> OpenClawConfigSnapshot:
        result = await self._request("config.get", {})
        return OpenClawConfigSnapshot(config=result["config"], hash=result["hash"])

    async def patch_config(self, patch: dict, base_hash: str) -> None:
        await self._request("config.patch", {"patch": patch, "baseHash": base_hash})

    async def chat_send(self, params: ChatSendParams) -> ChatSendResult:
        result = await self._request(
            "chat.send",
            {
                "key": params.session_key,
                "message": params.message,
                "agentId": params.agent_id,
            },
        )
        return ChatSendResult(**result)

    async def chat_history(self, session_key: str, limit: int = 50) -> list[ChatMessage]:
        result = await self._request("chat.history", {"key": session_key, "limit": limit})
        return [ChatMessage(**m) for m in result.get("messages", [])]

    async def sessions_list(
        self,
        agent_id: str | None = None,
        active_minutes: int | None = None,
        limit: int | None = None,
    ) -> list[SessionEntry]:
        params: dict = {}
        if agent_id:
            params["agentId"] = agent_id
        if active_minutes:
            params["activeMinutes"] = active_minutes
        if limit:
            params["limit"] = limit
        result = await self._request("sessions.list", params)
        return [SessionEntry(**s) for s in result.get("sessions", [])]

    # ─── Internal ────────────────────────────────────────

    async def _request(self, method: str, params: dict) -> dict:
        await self.connect()
        assert self._ws is not None

        self._request_id += 1
        req_id = self._request_id

        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._ws.send(json.dumps({"id": req_id, "method": method, "params": params}))

        try:
            return await asyncio.wait_for(future, timeout=self._options.request_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"Request timeout: {method} ({self._options.request_timeout}s)")

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_id = msg.get("id")
                if not isinstance(msg_id, int):
                    continue

                future = self._pending.pop(msg_id, None)
                if future is None or future.done():
                    continue

                if "error" in msg:
                    err = msg["error"]
                    future.set_exception(
                        Exception(err.get("message", f"RPC error {err.get('code')}"))
                    )
                else:
                    future.set_result(msg.get("result", {}))
        except websockets.ConnectionClosed:
            self._reject_all(Exception("WebSocket closed"))
        except asyncio.CancelledError:
            pass

    def _reject_all(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
