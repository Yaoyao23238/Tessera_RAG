/**
 * WebSocket 客户端：自动重连 + 按消息类型分发。
 *
 * 消息格式（共享知识 #3）：
 * - {"type":"task_progress","task_id","document_id","payload":{"status","progress","message"}}
 * - {"type":"model_download","payload":{"model","downloaded_mb","total_mb"}}
 * - {"type":"experiment_progress","experiment_id","payload":{"current","total"}}
 */

import { WS_BASE } from "./constants";

export interface WsMessage {
  type: string;
  task_id?: string;
  document_id?: string;
  experiment_id?: string;
  payload: Record<string, unknown>;
}

type MessageHandler = (msg: WsMessage) => void;

export class WsClient {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<MessageHandler>>();
  private retryDelay = 1000;
  private closed = false;
  private url: string;

  constructor(path = "/tasks") {
    this.url = `${WS_BASE}${path}`;
  }

  connect(): void {
    if (this.closed) return;
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.retryDelay = 1000;
    };
    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as WsMessage;
        this.handlers.get(msg.type)?.forEach((h) => h(msg));
        this.handlers.get("*")?.forEach((h) => h(msg));
      } catch {
        // 忽略非 JSON 消息
      }
    };
    this.ws.onclose = () => {
      if (!this.closed) {
        setTimeout(() => this.connect(), this.retryDelay);
        this.retryDelay = Math.min(this.retryDelay * 2, 15000);
      }
    };
  }

  /** 订阅某类消息，返回取消订阅函数。 */
  on(type: string, handler: MessageHandler): () => void {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type)!.add(handler);
    return () => this.handlers.get(type)?.delete(handler);
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
    this.handlers.clear();
  }
}
