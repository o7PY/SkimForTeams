// Keeps the phone ready for no connection (the student's request): as soon as the app is open and signed in, it loads the deck,
// History, Starred, reminders, teams and settings, and then the FULL detail of the last 50 cards plus every starred card, whether
// or not the student has looked at any of them. When a newer card pushes one out of the 50, its saved detail is dropped.
import { useQueryClient } from '@tanstack/react-query';
import * as Network from 'expo-network';
import { useCallback, useEffect, useRef } from 'react';
import { AppState } from 'react-native';

import { useAuth } from './auth';
import { flatten, type DeckData } from './deck-cache';
import { evictIds, keepIds, runPool, waitUntilQuiet } from './offline-window';
import { useOutbox } from './outbox-runtime';
import { queryKeys } from './query';
import { deckOptions, historyOptions, itemKey, itemOptions, meOptions, remindersOptions, starredOptions, subscriptionsOptions, teamCardsOptions } from './query-options';
import type { Card } from './types';

const EVERY_MS = 3 * 60_000;       // not more often than this when the app keeps coming to the front
const DETAIL_FRESH_MS = 30 * 60_000; // a saved card detail older than this is refreshed while online
const LANES = 3;                   // card details loaded at the same time, after the first few
const FIRST_FEW = 6;               // the newest cards (the ones a student opens first) load one at a time, so a screen the student opened is never starved
const TEAM_LANES = 2;              // team card lists loaded at the same time
const YIELD_MS = 6_000;            // wait at most this long for a screen the student is waiting on, then carry on regardless

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
      // Our own requests; anything else being fetched is a screen the student is waiting on, which always goes first.
      const mine = new Set<string>();
      const screenBusy = () => qc.isFetching({ predicate: (q) => !mine.has(q.queryHash) }) > 0;
      // Marked only while we are fetching it ourselves: a screen the student opened for the same data must still count as busy.
      const own = async (o: { queryKey: readonly unknown[] }, fn: () => Promise<unknown>) => {
        const h = JSON.stringify(o.queryKey);
        mine.add(h);
        try {
          await fn();
        } finally {
          mine.delete(h);
        }
      };
      try {
        await waitUntilQuiet(() => !screenBusy(), YIELD_MS);
        // 1. The lists. Skipped while changes are still waiting to be sent: the server's view would not have them yet.
        if (pendingRef.current === 0) {
          await Promise.allSettled([
            own(deckOptions(api), () => qc.prefetchInfiniteQuery({ ...deckOptions(api), pages: 1 })),
            own(historyOptions(api), () => qc.prefetchQuery(historyOptions(api))),
            own(starredOptions(api), () => qc.prefetchQuery(starredOptions(api))),
            own(remindersOptions(api), () => qc.prefetchQuery(remindersOptions(api))),
            own(subscriptionsOptions(api), () => qc.prefetchQuery(subscriptionsOptions(api))),
            own(meOptions(api), () => qc.prefetchQuery(meOptions(api))),
          ]);
        }
        // 2. Each course's first page of cards, so a team opens at once and in airplane mode. Read-only lists, so they do not wait
        //    for the outbox.
        const teams = qc.getQueryData<{ id: string }[]>(queryKeys.subscriptions) ?? [];
        await runPool(teams, TEAM_LANES, async (t) => {
          await waitUntilQuiet(() => !screenBusy(), YIELD_MS);
          await own(teamCardsOptions(api, t.id), () => qc.prefetchInfiniteQuery({ ...teamCardsOptions(api, t.id), pages: 1 }));
        });
        // 3. Which cards are worth keeping in full: the last 50 of everything, and every starred card.
        const deck = flatten(qc.getQueryData<DeckData>(queryKeys.deck));
        const history = qc.getQueryData<{ cards: Card[] }>(queryKeys.history)?.cards ?? [];
        const starred = qc.getQueryData<{ cards: Card[] }>(queryKeys.starred)?.cards ?? [];
        const keep = keepIds(deck, history, starred);
        // 4. Load the full detail of each. The newest few one at a time, the rest 3 at a time; ones loaded recently are left alone,
        //    and every one waits while the student is waiting on a screen.
        const load = async (id: number) => {
          await waitUntilQuiet(() => !screenBusy(), YIELD_MS);
          await own(itemOptions(api, id), () => qc.prefetchQuery({ ...itemOptions(api, id), staleTime: DETAIL_FRESH_MS }));
        };
        await runPool(keep.slice(0, FIRST_FEW), 1, load);
        await runPool(keep.slice(FIRST_FEW), LANES, load);
        // 5. Let go of the details of cards that have fallen out of the window.
        const cached = qc.getQueryCache().findAll({ queryKey: queryKeys.item }).map((q) => Number(q.queryKey[1])).filter(Number.isInteger);
        for (const id of evictIds(cached, keep)) qc.removeQueries({ queryKey: itemKey(id), exact: true });
        const saved = qc.getQueryCache().findAll({ queryKey: queryKeys.item }).filter((q) => q.state.data !== undefined).length;
        if (__DEV__) console.log(`[skim] offline cache: ${saved} of ${keep.length} cards saved in full (${deck.length} unread, ${history.length} read, ${starred.length} starred), ${teams.length} team lists`);
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
