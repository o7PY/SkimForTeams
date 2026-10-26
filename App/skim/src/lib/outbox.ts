// The offline mutation outbox (PHASE4.md P4-D-131). Swipes, stars, snoozes, reminder edits and settings changes are written here
// first, persisted, and replayed in order when the server can be reached. The server is the source of truth and the last write
// wins, so a newer operation on the same thing REPLACES an older pending one (dismiss then undo = just undo).
import { ApiError } from './api';

export type Method = 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export type OutboxOp = {
  id: string;
  /** operations with the same key are about the same thing; the newest wins */
  key: string;
  method: Method;
  path: string;
  body?: unknown;
  attempts: number;
  createdAt: number;
};

export type NewOp = { key: string; method: Method; path: string; body?: unknown; /** merge object bodies instead of replacing (settings) */ merge?: boolean };

export type Rejected = { op: OutboxOp; status: number; reason: string };
export type FlushResult = { status: 'done' | 'offline' | 'unauthorized' | 'retry' | 'busy'; sent: number; rejected: Rejected[] };

export type KeyValue = {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  removeItem(key: string): Promise<void>;
};

export const MAX_OPS = 500;
export const MAX_ATTEMPTS = 5;

export const storageKey = (userId: string) => `skim-outbox:${userId}`;

export function deepMerge(base: unknown, over: unknown): unknown {
  if (isObject(base) && isObject(over)) {
    const out: Record<string, unknown> = { ...base };
    for (const [k, v] of Object.entries(over)) out[k] = k in base ? deepMerge(base[k], v) : v;
    return out;
  }
  return over; // arrays and scalars: the newer value replaces
}
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

export class Outbox {
  private ops: OutboxOp[] = [];
  private userId: string | null = null;
  private inflight: string | null = null;
  private flushing = false;
  private saving: Promise<void> = Promise.resolve();
  private listeners = new Set<() => void>();
  private changes = 0;
  /** operations dropped for good (a 4xx, too many failures, or a full queue): the UI may mention them once */
  dropped: Rejected[] = [];

  constructor(
    private store: KeyValue,
    private send: (op: OutboxOp) => Promise<void>,
    private opts: { uuid?: () => string; now?: () => number; maxAttempts?: number; maxOps?: number } = {},
  ) {}

  private uuid = () => (this.opts.uuid ?? (() => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`))();
  private now = () => (this.opts.now ?? Date.now)();

  /** Read this user's saved queue. A corrupt or foreign file is ignored, never trusted. */
  async load(userId: string): Promise<void> {
    this.userId = userId;
    this.ops = [];
    try {
      const raw = await this.store.getItem(storageKey(userId));
      const v = raw ? JSON.parse(raw) : null;
      if (v && v.userId === userId && Array.isArray(v.ops)) this.ops = v.ops.filter(validOp);
    } catch {
      this.ops = [];
    }
    this.emit();
  }

  get size(): number {
    return this.ops.length;
  }
  snapshot(): OutboxOp[] {
    return this.ops.map((o) => ({ ...o }));
  }
  has(key: string): boolean {
    return this.ops.some((o) => o.key === key);
  }
  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  /** bumps on every change, so a UI can subscribe to a cheap number and re-read the queue only when it moved */
  get version(): number {
    return this.changes;
  }
  private emit() {
    this.changes++;
    for (const fn of this.listeners) fn();
  }

  private persist(): Promise<void> {
    if (!this.userId) return Promise.resolve();
    const data = JSON.stringify({ userId: this.userId, ops: this.ops });
    const key = storageKey(this.userId);
    this.saving = this.saving.then(() => this.store.setItem(key, data)).catch(() => undefined);
    return this.saving;
  }

  async enqueue(n: NewOp): Promise<void> {
    if (!this.userId) throw new Error('outbox is not loaded');
    const prior = this.ops.find((o) => o.key === n.key && o.id !== this.inflight);
    let body = n.body;
    if (n.merge && prior && isObject(prior.body) && isObject(body)) body = deepMerge(prior.body, body);
    this.ops = this.ops.filter((o) => !(o.key === n.key && o.id !== this.inflight));
    this.ops.push({ id: this.uuid(), key: n.key, method: n.method, path: n.path, body, attempts: 0, createdAt: this.now() });
    const max = this.opts.maxOps ?? MAX_OPS;
    while (this.ops.length > max) {
      const oldest = this.ops.find((o) => o.id !== this.inflight)!;
      this.ops = this.ops.filter((o) => o !== oldest);
      this.dropped.push({ op: oldest, status: 0, reason: 'queue_full' });
    }
    this.emit();
    await this.persist();
  }

  /** Stop using this user's queue WITHOUT deleting it (a forced sign-out: the same user signing back in resumes where they left off). */
  detach(): void {
    this.ops = [];
    this.userId = null;
    this.emit();
  }

  /** Forget everything (a deliberate sign-out). */
  async clear(): Promise<void> {
    const key = this.userId ? storageKey(this.userId) : null;
    this.ops = [];
    this.userId = null;
    this.emit();
    await this.saving;
    if (key) await this.store.removeItem(key).catch(() => undefined);
  }

  /** Replay in order. Stops at the first operation that cannot be delivered NOW; drops the ones the server will never accept. */
  async flush(): Promise<FlushResult> {
    if (this.flushing) return { status: 'busy', sent: 0, rejected: [] };
    this.flushing = true;
    const rejected: Rejected[] = [];
    let sent = 0;
    const max = this.opts.maxAttempts ?? MAX_ATTEMPTS;
    try {
      for (;;) {
        const op = this.ops[0];
        if (!op) return { status: 'done', sent, rejected };
        this.inflight = op.id;
        try {
          await this.send(op);
          this.ops = this.ops.filter((o) => o.id !== op.id);
          sent++;
          this.emit();
          await this.persist();
        } catch (e) {
          const status = e instanceof ApiError ? e.status : -1; // -1: something unexpected; treated like a server hiccup
          if (status === 0) return { status: 'offline', sent, rejected };
          if (status === 401) return { status: 'unauthorized', sent, rejected };
          if (status === 429 || status >= 500 || status === -1) {
            op.attempts++;
            if (op.attempts < max) {
              await this.persist();
              return { status: 'retry', sent, rejected };
            }
          }
          // a 4xx (the item is gone, the input was refused...) or too many failures: the server will not take it. Drop it and go on.
          const r: Rejected = { op: { ...op }, status, reason: e instanceof ApiError ? (e.body?.error ?? e.message) : 'unexpected_error' };
          rejected.push(r);
          this.dropped.push(r);
          this.ops = this.ops.filter((o) => o.id !== op.id);
          this.emit();
          await this.persist();
        } finally {
          this.inflight = null;
        }
      }
    } finally {
      this.flushing = false;
    }
  }
}

function validOp(o: unknown): o is OutboxOp {
  const v = o as OutboxOp;
  return (
    !!v && typeof v.id === 'string' && typeof v.key === 'string' && typeof v.path === 'string' && v.path.startsWith('/') &&
    ['POST', 'PUT', 'PATCH', 'DELETE'].includes(v.method) && typeof v.attempts === 'number'
  );
}
