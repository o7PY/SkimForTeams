// The TanStack Query cache, persisted per account (P4-D-131): the deck and detail still open in airplane mode.
import AsyncStorage from '@react-native-async-storage/async-storage';
import { createAsyncStoragePersister } from '@tanstack/query-async-storage-persister';
import { QueryClient } from '@tanstack/react-query';

import { ApiError } from './api';
import { MAX_SAVED_CHARS, pruneForSize } from './offline-window';

export const CACHE_MAX_AGE = 30 * 24 * 60 * 60 * 1000; // the phone remembers for a month without a connection
export const CACHE_BUSTER = 'v1'; // bump when a cached payload's shape changes, so old caches are discarded instead of misread
export const cacheKey = (userId: string) => `skim-query-cache:${userId}`;

export const queryKeys = {
  subscriptions: ['subscriptions'] as const,
  deck: ['deck'] as const,
  history: ['history'] as const,
  starred: ['starred'] as const,
  reminders: ['reminders'] as const,
  me: ['me'] as const,
  teamCards: ['team-cards'] as const, // ['team-cards', teamId]: one team's full card list (the Teams tab); never saved for offline
  item: ['item'] as const, // ['item', id] is one opened card; invalidating this prefix refetches them all
};

/** Retry network blips and server errors a couple of times; never retry a 4xx (it will not change). */
export function shouldRetry(failures: number, e: unknown): boolean {
  if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 429) return false;
  return failures < 2;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, gcTime: CACHE_MAX_AGE, networkMode: 'offlineFirst', retry: shouldRetry, refetchOnReconnect: true },
      mutations: { networkMode: 'offlineFirst' },
    },
  });
}

export const createPersister = (userId: string) =>
  createAsyncStoragePersister({
    storage: AsyncStorage,
    key: cacheKey(userId),
    throttleTime: 1000,
    // The whole cache is ONE saved value and Android cannot read one above ~2 MB back: trim the oldest opened-card details first.
    serialize: (client) => JSON.stringify(pruneForSize(client, MAX_SAVED_CHARS)),
  });
