// Team actions from Settings: mute (queued, works offline), leave (immediate, asks first), and deleting the whole account.
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';

import { ApiError } from './api';
import { useAuth } from './auth';
import { stopBackgroundSync } from './background';
import { cancelAllReminders } from './notif-adapter';
import { outboxRef, useOutbox } from './outbox-runtime';
import { usePush } from './push-runtime';
import { cacheKey, queryKeys } from './query';
import { STATE_KEY } from './reminder-plan';
import type { TeamInfo } from './join';

export function useTeamActions() {
  const { api } = useAuth();
  const qc = useQueryClient();
  const { enqueue } = useOutbox();
  return {
    async setMuted(team: TeamInfo, muted: boolean) {
      qc.setQueryData<TeamInfo[]>(queryKeys.subscriptions, (list) => list?.map((t) => (t.id === team.id ? { ...t, muted } : t)));
      await enqueue({ key: `mute:${team.id}`, method: 'PATCH', path: `/subscriptions/${encodeURIComponent(team.id)}`, body: { muted } });
    },
    /** Leaving removes the team's cards, history, stars and reminders for this student: needs the server, so no offline queue. */
    async leave(team: TeamInfo): Promise<{ ok: true } | { ok: false; message: string }> {
      try {
        await api.delete(`/subscriptions/${encodeURIComponent(team.id)}`);
      } catch (e) {
        return { ok: false, message: e instanceof ApiError && e.isNetwork ? 'No connection. Try again when you’re online.' : 'Couldn’t leave the team. Please try again.' };
      }
      for (const key of Object.values(queryKeys)) void qc.invalidateQueries({ queryKey: key });
      return { ok: true };
    },
  };
}

export function useDeleteAccount() {
  const { api, session, signOut } = useAuth();
  const push = usePush();
  const qc = useQueryClient();
  const userId = session?.user.id ?? null;
  return useCallback(async (): Promise<{ ok: true } | { ok: false; message: string }> => {
    await push.unregister(); // best effort: the account is about to go, so its device row goes with it either way
    try {
      await api.delete('/me');
    } catch (e) {
      // Nothing is wiped locally unless the server really deleted the account.
      return { ok: false, message: e instanceof ApiError && e.isNetwork ? 'No connection. Your account was not deleted.' : 'Couldn’t delete the account. Nothing was changed; please try again.' };
    }
    await outboxRef.current?.clear();
    await stopBackgroundSync();
    await cancelAllReminders().catch(() => undefined);
    qc.clear();
    if (userId) {
      await AsyncStorage.removeItem(cacheKey(userId)).catch(() => undefined);
      await AsyncStorage.removeItem(STATE_KEY(userId)).catch(() => undefined);
    }
    await signOut();
    return { ok: true };
  }, [api, push, qc, signOut, userId]);
}
