// One definition of every request the app caches. The screens and the background prefetch (offline-runtime) both use these, so they
// share cache entries: what the prefetch loaded is exactly what a screen shows, with no second request.
import { infiniteQueryOptions, queryOptions } from '@tanstack/react-query';

import type { Api, Me } from './api';
import type { TeamInfo } from './join';
import { queryKeys } from './query';
import type { RemindersResponse } from './reminder-plan';
import type { Card, DeckPage } from './types';

export const DECK_PAGE = 50; // the first page already holds the 50 cards the phone keeps for offline use

export const deckOptions = (api: Api) =>
  infiniteQueryOptions({
    queryKey: queryKeys.deck,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => api.get<DeckPage>(`/deck?limit=${DECK_PAGE}${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ''}`),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

export const historyOptions = (api: Api) => queryOptions({ queryKey: queryKeys.history, queryFn: () => api.get<{ cards: Card[] }>('/history') });
export const starredOptions = (api: Api) => queryOptions({ queryKey: queryKeys.starred, queryFn: () => api.get<{ cards: Card[] }>('/starred') });
export const remindersOptions = (api: Api) => queryOptions({ queryKey: queryKeys.reminders, queryFn: () => api.get<RemindersResponse>('/reminders?days=30') });
export const meOptions = (api: Api) => queryOptions({ queryKey: queryKeys.me, queryFn: () => api.get<Me>('/me') });
export const subscriptionsOptions = (api: Api) =>
  queryOptions({ queryKey: queryKeys.subscriptions, queryFn: async () => (await api.get<{ teams: TeamInfo[] }>('/subscriptions')).teams });

export const TEAM_CARDS_PAGE = 30;
export const teamCardsOptions = (api: Api, teamId: string) =>
  infiniteQueryOptions({
    queryKey: [...queryKeys.teamCards, teamId] as const,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api.get<DeckPage>(`/teams/${encodeURIComponent(teamId)}/cards?limit=${TEAM_CARDS_PAGE}${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ''}`),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

export const itemKey = (id: number | string) => [...queryKeys.item, String(id)] as const;
export const itemOptions = (api: Api, id: number | string) =>
  queryOptions({ queryKey: itemKey(id), queryFn: () => api.get<Card>(`/items/${encodeURIComponent(String(id))}`) });
