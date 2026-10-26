// Background sync (PHASE4.md P4-D-132): about every 15 minutes or more, Android runs this even with the app closed. It makes one cheap
// /sync call and, only if the reminders changed, refetches them and reschedules the local notifications. Best effort: the app also
// syncs every time it is opened. The task is defined at MODULE scope (imported by the root layout) so it exists when Android starts the
// app headlessly.
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as BackgroundTask from 'expo-background-task';
import * as TaskManager from 'expo-task-manager';

import { createApi } from './api';
import { loaded } from './config';
import { realScheduler } from './notif-adapter';
import { backgroundSync, syncReminders, type RemindersResponse, type SyncResponse } from './reminder-plan';
import { getSupabase } from './supabase';

export const BACKGROUND_TASK = 'skim-background-sync';
const MINUTES = 15; // the platform minimum

async function runOnce(): Promise<'ok' | 'failed'> {
  if (!('config' in loaded)) return 'failed';
  const supabase = getSupabase();
  const api = createApi({
    baseUrl: loaded.config.apiBase,
    timeoutMs: 15_000,
    tokens: {
      getToken: async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
      refresh: async () => (await supabase.auth.refreshSession()).data.session?.access_token ?? null,
    },
  });
  const r = await backgroundSync({
    userId: async () => (await supabase.auth.getSession()).data.session?.user.id ?? null,
    store: AsyncStorage,
    fetchSync: (since) => api.get<SyncResponse>(`/sync${since ? `?since=${encodeURIComponent(since)}` : ''}`),
    syncReminders: () => syncReminders({ fetchReminders: () => api.get<RemindersResponse>('/reminders?days=30'), scheduler: realScheduler }),
  });
  if (__DEV__) console.log('[skim] background sync:', r);
  return r === 'failed' ? 'failed' : 'ok';
}

TaskManager.defineTask(BACKGROUND_TASK, async () => {
  try {
    return (await runOnce()) === 'ok' ? BackgroundTask.BackgroundTaskResult.Success : BackgroundTask.BackgroundTaskResult.Failed;
  } catch {
    return BackgroundTask.BackgroundTaskResult.Failed;
  }
});

export async function startBackgroundSync(): Promise<void> {
  try {
    if ((await BackgroundTask.getStatusAsync()) === BackgroundTask.BackgroundTaskStatus.Restricted) return;
    if (!(await TaskManager.isTaskRegisteredAsync(BACKGROUND_TASK))) await BackgroundTask.registerTaskAsync(BACKGROUND_TASK, { minimumInterval: MINUTES });
  } catch (e) {
    if (__DEV__) console.log('[skim] background sync could not be registered:', e instanceof Error ? e.name : 'error');
  }
}

export async function stopBackgroundSync(): Promise<void> {
  try {
    if (await TaskManager.isTaskRegisteredAsync(BACKGROUND_TASK)) await BackgroundTask.unregisterTaskAsync(BACKGROUND_TASK);
  } catch {
    // nothing to stop
  }
}
