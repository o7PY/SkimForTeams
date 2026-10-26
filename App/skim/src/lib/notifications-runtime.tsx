// Keeps the phone's reminders in step with the server, reacts to pushes, and opens the right card when a notification is tapped
// (PHASE4.md 7.3). One component, mounted inside the navigator while signed in. The rules live in lib/reminder-plan.ts.
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import * as Notifications from 'expo-notifications';
import { router, useRootNavigationState } from 'expo-router';
import { useEffect, useRef } from 'react';
import { AppState } from 'react-native';

import { useAuth } from './auth';
import { startBackgroundSync } from './background';
import { realScheduler } from './notif-adapter';
import { queryKeys } from './query';
import { remindersOptions } from './query-options';
import { isSkimNotification, readState, routeForNotification, syncReminders, writeState } from './reminder-plan';

const MIN_GAP_MS = 20_000; // don't refetch more often than this when the app flips between foreground and background

export function NotificationsRuntime() {
  const { api, session, status } = useAuth();
  const qc = useQueryClient();
  const userId = session?.user.id ?? null;
  const signedIn = status === 'signedIn' && !!userId;

  // 1. The server's reminders, kept fresh. Whenever they change, the local notifications are made to match.
  const reminders = useQuery({ ...remindersOptions(api), enabled: signedIn, staleTime: MIN_GAP_MS });
  const scheduledFor = useRef<string | null>(null);
  useEffect(() => {
    const data = reminders.data;
    if (!signedIn || !userId || !data) return;
    const stamp = `${userId}:${data.etag}:${reminders.dataUpdatedAt}`;
    if (scheduledFor.current === stamp) return;
    scheduledFor.current = stamp;
    void (async () => {
      const r = await syncReminders({ fetchReminders: async () => data, scheduler: realScheduler });
      if (__DEV__) console.log('[skim] reminders scheduled:', r.status, r.status === 'scheduled' ? `${r.scheduled} scheduled, ${r.cancelled} cancelled` : '');
      if (r.status === 'scheduled') {
        const s = await readState(AsyncStorage, userId);
        await writeState(AsyncStorage, userId, { ...s, etag: r.etag }); // so the background task knows what the phone already has
      }
    })();
  }, [signedIn, userId, reminders.data, reminders.dataUpdatedAt]);

  // 2. Background sync while signed in.
  useEffect(() => {
    if (signedIn) void startBackgroundSync();
  }, [signedIn]);

  // 3. Back in the foreground: refresh reminders, and the deck if the background task saw new cards.
  const lastActive = useRef(0);
  useEffect(() => {
    if (!signedIn || !userId) return;
    const sub = AppState.addEventListener('change', (s) => {
      if (s !== 'active' || Date.now() - lastActive.current < MIN_GAP_MS) return;
      lastActive.current = Date.now();
      void qc.invalidateQueries({ queryKey: queryKeys.reminders });
      void readState(AsyncStorage, userId).then((st) => {
        if (!st.deckStale) return;
        void qc.invalidateQueries({ queryKey: queryKeys.deck });
        void writeState(AsyncStorage, userId, { ...st, deckStale: false });
      });
    });
    return () => sub.remove();
  }, [signedIn, userId, qc]);

  // 4. A push arrives while the app is open: refresh everything it may have changed (the system shows its banner).
  useEffect(() => {
    if (!signedIn) return;
    const sub = Notifications.addNotificationReceivedListener((n) => {
      if (!isSkimNotification(n.request.content.data)) return;
      for (const key of [queryKeys.deck, queryKeys.reminders, queryKeys.item, queryKeys.history, queryKeys.starred]) void qc.invalidateQueries({ queryKey: key });
    });
    return () => sub.remove();
  }, [signedIn, qc]);

  // 5. A notification was tapped (also when it is what launched the app): open its card.
  const last = Notifications.useLastNotificationResponse();
  const navReady = !!useRootNavigationState()?.key;
  const handled = useRef<string | null>(null);
  useEffect(() => {
    if (!last || !signedIn || !navReady) return;
    const stamp = `${last.notification.request.identifier}:${last.notification.date}`;
    if (handled.current === stamp) return;
    handled.current = stamp;
    const data = last.notification.request.content.data;
    if (!isSkimNotification(data)) return;
    void qc.invalidateQueries({ queryKey: queryKeys.deck });
    const target = routeForNotification(data);
    if (target) router.push(target);
    else router.navigate('/');
    void Notifications.clearLastNotificationResponse();
  }, [last, signedIn, navReady, qc]);

  return null;
}
