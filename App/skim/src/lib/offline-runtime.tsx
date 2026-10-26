// Keeps the phone ready for no connection (the student's request): as soon as the app is open and signed in, it loads the deck,
// History, Starred, reminders, teams and settings, and then the FULL detail of the last 50 cards plus every starred card, whether
// or not the student has looked at any of them. When a newer card pushes one out of the 50, its saved detail is dropped.
import { useQueryClient } from '@tanstack/react-query';
import * as Network from 'expo-network';
import { useCallback, useEffect, useRef } from 'react';
import { AppState } from 'react-native';

import { useAuth } from './auth';
import { flatten, type DeckData } from './deck-cache';
import { evictIds, keepIds, runPool } from './offline-window';
import { useOutbox } from './outbox-runtime';
import { queryKeys } from './query';
import { deckOptions, historyOptions, itemKey, itemOptions, meOptions, remindersOptions, starredOptions, subscriptionsOptions } from './query-options';
import type { Card } from './types';

const EVERY_MS = 3 * 60_000;       // not more often than this when the app keeps coming to the front
const DETAIL_FRESH_MS = 30 * 60_000; // a saved card detail older than this is refreshed while online
const LANES = 3;                   // card details loaded at the same time

export function OfflineSync() {
  const { api, session, status } = useAuth();
  const qc = useQueryClient();
  const { pending } = useOutbox();
  const userId = session?.user.id ?? null;
  const signedIn = status === 'signedIn' && !!userId;
  const pendingRef = useRef(pending);
  const running = useRef(false);
  const lastRun = useRef(0);
  useEffect(() => {
    pendingRef.current = pending;
  }, [pending]);

  const run = useCallback(
    async (force: boolean) => {
      if (!signedIn || running.current || (!force && Date.now() - lastRun.current < EVERY_MS)) return;
      const net = await Network.getNetworkStateAsync().catch(() => null);
      if (!net?.isConnected) {
        if (__DEV__) console.log('[skim] offline cache: skipped (no connection)');
        return;
      }
      if (__DEV__) console.log(`[skim] offline cache: loading (${force ? 'app start' : 'refresh'})`);
      running.current = true;
      lastRun.current = Date.now();
      try {
        // 1. The lists. Skipped while changes are still waiting to be sent: the server's view would not have them yet.
        if (pendingRef.current === 0) {
          await Promise.allSettled([
            qc.prefetchInfiniteQuery({ ...deckOptions(api), pages: 1 }),
            qc.prefetchQuery(historyOptions(api)),
            qc.prefetchQuery(starredOptions(api)),
            qc.prefetchQuery(remindersOptions(api)),
            qc.prefetchQuery(subscriptionsOptions(api)),
            qc.prefetchQuery(meOptions(api)),
          ]);
        }
        // 2. Which cards are worth keeping in full: the last 50 of everything, and every starred card.
        const deck = flatten(qc.getQueryData<DeckData>(queryKeys.deck));
        const history = qc.getQueryData<{ cards: Card[] }>(queryKeys.history)?.cards ?? [];
        const starred = qc.getQueryData<{ cards: Card[] }>(queryKeys.starred)?.cards ?? [];
        const keep = keepIds(deck, history, starred);
        // 3. Load the full detail of each (3 at a time). Ones loaded recently are left alone.
        await runPool(keep, LANES, async (id) => {
          await qc.prefetchQuery({ ...itemOptions(api, id), staleTime: DETAIL_FRESH_MS });
        });
        // 4. Let go of the details of cards that have fallen out of the window.
        const cached = qc.getQueryCache().findAll({ queryKey: queryKeys.item }).map((q) => Number(q.queryKey[1])).filter(Number.isInteger);
        for (const id of evictIds(cached, keep)) qc.removeQueries({ queryKey: itemKey(id), exact: true });
        const saved = qc.getQueryCache().findAll({ queryKey: queryKeys.item }).filter((q) => q.state.data !== undefined).length;
        if (__DEV__) console.log(`[skim] offline cache: ${saved} of ${keep.length} cards saved in full (${deck.length} unread, ${history.length} read, ${starred.length} starred)`);
      } catch (e) {
        // offline halfway through, or the server was busy: the next run carries on
        if (__DEV__) console.log('[skim] offline cache: stopped early,', e instanceof Error ? e.name : 'error');
      } finally {
        running.current = false;
      }
    },
    [signedIn, api, qc],
  );

  // On sign-in / app start (before any tab is opened), whenever the app comes back to the front, and when the connection returns.
  useEffect(() => {
    if (!signedIn) return;
    void run(true);
    const app = AppState.addEventListener('change', (s) => s === 'active' && void run(false));
    const net = Network.addNetworkStateListener((s) => s.isConnected && void run(false));
    return () => {
      app.remove();
      net.remove();
    };
  }, [signedIn, run]);

  // Also right after the outbox has been delivered (the lists were held back while it was not empty).
  const wasPending = useRef(false);
  useEffect(() => {
    if (wasPending.current && pending === 0) void run(true);
    wasPending.current = pending > 0;
  }, [pending, run]);

  return null;
}
