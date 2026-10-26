import AsyncStorage from '@react-native-async-storage/async-storage';
import { useQueryClient } from '@tanstack/react-query';
import * as Network from 'expo-network';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { AppState } from 'react-native';

import type { Api } from './api';
import { useAuth } from './auth';
import { Outbox, type NewOp, type OutboxOp } from './outbox';
import { queryKeys } from './query';

const RETRY_MS = 30_000;

type OutboxApi = {
  /** Record a change; it is sent now if there is a connection, otherwise when there is one. */
  enqueue(op: NewOp): Promise<void>;
  flush(): Promise<void>;
  pending: number;
  /** the operations still waiting to be delivered (for hiding cards the student already acted on) */
  ops: OutboxOp[];
  /** a one-line note when some changes could not be saved (shown once) */
  notice: string | null;
  dismissNotice(): void;
};

const Ctx = createContext<OutboxApi | null>(null);

export function sendOp(api: Api, op: OutboxOp): Promise<unknown> {
  switch (op.method) {
    case 'POST': return api.post(op.path, op.body);
    case 'PUT': return api.put(op.path, op.body);
    case 'PATCH': return api.patch(op.path, op.body);
    case 'DELETE': return api.delete(op.path);
  }
}

export const outboxRef: { current: Outbox | null } = { current: null }; // for the sign-out flow, which lives outside the provider tree

export function OutboxProvider({ userId, children }: { userId: string | null; children: ReactNode }) {
  const { api } = useAuth();
  const qc = useQueryClient();
  const box = useMemo(() => new Outbox(AsyncStorage, (op) => sendOp(api, op) as Promise<void>), [api]);
  const [notice, setNotice] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flushRef = useRef<() => Promise<void>>(async () => undefined); // the retry timer calls the latest flush

  const subscribe = useCallback((fn: () => void) => box.subscribe(fn), [box]);
  const pending = useSyncExternalStore(subscribe, () => box.size);
  const version = useSyncExternalStore(subscribe, () => box.version);
  const ops = useMemo(() => box.snapshot(), [box, version]); // eslint-disable-line react-hooks/exhaustive-deps -- re-read only when the queue moved

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const r = await box.flush();
    if (r.sent > 0) {
      // The server is the source of truth: refetch so what the phone shows is what the server now holds (last write wins).
      for (const key of Object.values(queryKeys)) void qc.invalidateQueries({ queryKey: key });
    }
    if (r.rejected.length > 0) setNotice('Some changes could not be saved because the item is no longer available.');
    if ((r.status === 'offline' || r.status === 'retry') && box.size > 0) timer.current = setTimeout(() => void flushRef.current(), RETRY_MS);
  }, [box, qc]);

  useEffect(() => {
    flushRef.current = flush;
  }, [flush]);

  useEffect(() => {
    outboxRef.current = box;
    if (!userId) {
      box.detach();
      return;
    }
    let alive = true;
    void box.load(userId).then(() => {
      if (alive && box.size > 0) void flush();
    });
    const net = Network.addNetworkStateListener((s) => s.isConnected && void flush());
    const app = AppState.addEventListener('change', (s) => s === 'active' && void flush());
    return () => {
      alive = false;
      net.remove();
      app.remove();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [box, userId, flush]);

  const enqueue = useCallback(
    async (op: NewOp) => {
      await box.enqueue(op);
      void flush();
    },
    [box, flush],
  );

  const value = useMemo<OutboxApi>(() => ({ enqueue, flush: async () => void (await flush()), pending, ops, notice, dismissNotice: () => setNotice(null) }), [enqueue, flush, pending, ops, notice]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useOutbox(): OutboxApi {
  const v = useContext(Ctx);
  if (!v) throw new Error('useOutbox must be used inside <OutboxProvider>');
  return v;
}
