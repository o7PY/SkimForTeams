// The Reminders list (inside Starred): every reminder in the next 60 days with an on/off switch. Same pattern as the other lists:
// change the screen at once, queue the change in the outbox (so it also works offline), let the refetch after delivery settle it.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';

import { useAuth } from './auth';
import { useOutbox } from './outbox-runtime';
import { queryKeys } from './query';
import { remindersAllOptions } from './query-options';
import type { ListedReminder, RemindersAllResponse } from './reminder-plan';

/** Pure: the list with one reminder switched. */
export function withEnabled(data: RemindersAllResponse | undefined, id: string, enabled: boolean): RemindersAllResponse | undefined {
  return data ? { ...data, reminders: data.reminders.map((r) => (r.id === id ? { ...r, enabled } : r)) } : data;
}

/** The newest switch the outbox still holds for a reminder, so a refresh cannot flip the screen back before it is delivered. */
export function pendingSwitches(ops: readonly { method: string; path: string; body?: unknown }[]): Map<string, boolean> {
  const out = new Map<string, boolean>();
  for (const op of ops) {
    const m = /^\/reminders\/([0-9a-f-]{36})$/i.exec(op.path);
    if (!m) continue;
    if (op.method === 'PATCH' && typeof (op.body as { enabled?: unknown } | undefined)?.enabled === 'boolean') out.set(m[1], (op.body as { enabled: boolean }).enabled);
    if (op.method === 'DELETE') out.set(m[1], false);
  }
  return out;
}

export function useReminderList() {
  const { api } = useAuth();
  const { ops, pending } = useOutbox();
  // While a switch is still waiting to be sent, a refresh would show the server's OLD view and flip it back: hold still until delivered.
  const q = useQuery({ ...remindersAllOptions(api), ...(pending === 0 ? {} : { refetchOnMount: false as const, refetchOnReconnect: false as const }) });
  const held = useMemo(() => pendingSwitches(ops), [ops]);
  const reminders = useMemo<ListedReminder[]>(() => (q.data?.reminders ?? []).map((r) => (held.has(r.id) ? { ...r, enabled: held.get(r.id) as boolean } : r)), [q.data, held]);
  return { ...q, reminders };
}

export function useReminderSwitch() {
  const qc = useQueryClient();
  const { enqueue } = useOutbox();
  return useCallback(
    async (id: string, enabled: boolean) => {
      qc.setQueryData<RemindersAllResponse>(queryKeys.remindersAll, (d) => withEnabled(d, id, enabled));
      await enqueue({ key: `reminder:${id}`, method: 'PATCH', path: `/reminders/${id}`, body: { enabled } });
    },
    [qc, enqueue],
  );
}
