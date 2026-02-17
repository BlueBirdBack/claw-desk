/**
 * OpenClaw Gateway Client
 *
 * Communicates with an OpenClaw gateway instance via its WebSocket API.
 * This is how ClawDesk manages agents (tenants) and routes messages.
 *
 * Protocol: OpenClaw uses a JSON-RPC-style WebSocket protocol.
 * Each request has { id, method, params }, response has { id, result/error }.
 */

import { WebSocket } from 'ws';
import type {
  OpenClawGatewayClient,
  OpenClawConfigSnapshot,
  ChatSendParams,
  ChatSendResult,
  ChatMessage,
  SessionsListParams,
  SessionEntry,
} from '@clawdesk/shared';

export interface GatewayClientOptions {
  /** WebSocket URL of the OpenClaw gateway (e.g., ws://localhost:3001) */
  url: string;
  /** Gateway auth token */
  token: string;
  /** Connection timeout in ms (default: 5000) */
  connectTimeoutMs?: number;
  /** Request timeout in ms (default: 30000) */
  requestTimeoutMs?: number;
}

type PendingRequest = {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

export class GatewayClient implements OpenClawGatewayClient {
  private _options: Required<GatewayClientOptions>;
  private _ws: WebSocket | null = null;
  private _connected = false;
  private _requestId = 0;
  private _pending = new Map<number, PendingRequest>();
  private _connectPromise: Promise<void> | null = null;

  constructor(options: GatewayClientOptions) {
    this._options = {
      connectTimeoutMs: 5000,
      requestTimeoutMs: 30000,
      ...options,
    };
  }

  // ─── Connection ──────────────────────────────────────

  async connect(): Promise<void> {
    if (this._connected && this._ws?.readyState === WebSocket.OPEN) {
      return;
    }

    if (this._connectPromise) {
      return this._connectPromise;
    }

    this._connectPromise = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        this._ws?.close();
        reject(new Error(`Connection timeout after ${this._options.connectTimeoutMs}ms`));
      }, this._options.connectTimeoutMs);

      const ws = new WebSocket(this._options.url, {
        headers: { Authorization: `Bearer ${this._options.token}` },
      });

      ws.on('open', () => {
        clearTimeout(timer);
        this._ws = ws;
        this._connected = true;
        this._connectPromise = null;
        resolve();
      });

      ws.on('message', (data) => this._handleMessage(data.toString()));

      ws.on('close', () => {
        this._connected = false;
        this._rejectAllPending(new Error('WebSocket closed'));
      });

      ws.on('error', (err) => {
        clearTimeout(timer);
        this._connectPromise = null;
        reject(err);
      });
    });

    return this._connectPromise;
  }

  async disconnect(): Promise<void> {
    if (this._ws) {
      this._ws.close();
      this._ws = null;
      this._connected = false;
    }
  }

  get connected(): boolean {
    return this._connected;
  }

  // ─── OpenClaw API Methods ────────────────────────────

  async getConfig(): Promise<OpenClawConfigSnapshot> {
    const result = await this._request('config.get', {});
    return {
      config: (result as Record<string, unknown>).config as Record<string, unknown>,
      hash: (result as Record<string, unknown>).hash as string,
    };
  }

  async patchConfig(patch: Record<string, unknown>, baseHash: string): Promise<void> {
    await this._request('config.patch', { patch, baseHash });
  }

  async chatSend(params: ChatSendParams): Promise<ChatSendResult> {
    const result = await this._request('chat.send', {
      key: params.sessionKey,
      message: params.message,
      agentId: params.agentId,
    });
    return result as ChatSendResult;
  }

  async chatHistory(sessionKey: string, limit = 50): Promise<ChatMessage[]> {
    const result = await this._request('chat.history', {
      key: sessionKey,
      limit,
    });
    return (result as { messages?: ChatMessage[] }).messages ?? [];
  }

  async sessionsList(params: SessionsListParams = {}): Promise<SessionEntry[]> {
    const result = await this._request('sessions.list', params);
    return (result as { sessions?: SessionEntry[] }).sessions ?? [];
  }

  // ─── Internal ────────────────────────────────────────

  private async _request(method: string, params: Record<string, unknown>): Promise<unknown> {
    await this.connect();

    const id = ++this._requestId;

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pending.delete(id);
        reject(new Error(`Request timeout: ${method} (${this._options.requestTimeoutMs}ms)`));
      }, this._options.requestTimeoutMs);

      this._pending.set(id, { resolve, reject, timer });

      this._ws!.send(JSON.stringify({ id, method, params }));
    });
  }

  private _handleMessage(raw: string): void {
    let msg: { id?: number; result?: unknown; error?: unknown };
    try {
      msg = JSON.parse(raw);
    } catch {
      return; // Ignore non-JSON messages (events, etc.)
    }

    if (typeof msg.id !== 'number') {
      return; // Ignore events/notifications
    }

    const pending = this._pending.get(msg.id);
    if (!pending) {
      return;
    }

    this._pending.delete(msg.id);
    clearTimeout(pending.timer);

    if (msg.error) {
      const errObj = msg.error as { message?: string; code?: number };
      pending.reject(new Error(errObj.message ?? `RPC error ${errObj.code}`));
    } else {
      pending.resolve(msg.result);
    }
  }

  private _rejectAllPending(error: Error): void {
    for (const [id, pending] of this._pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
      this._pending.delete(id);
    }
  }
}
