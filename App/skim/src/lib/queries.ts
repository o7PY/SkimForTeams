import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { useAuth } from './auth';
import { meOptions, subscriptionsOptions, teamCardsOptions } from './query-options';

export function useSubscriptions() {
  const { api } = useAuth();
  return useQuery(subscriptionsOptions(api));
}

export function useMe() {
  const { api } = useAuth();
  return useQuery(meOptions(api));
}

/** Every card of one team, newest first, paged (the Teams tab). Read-only: the cards carry no swipe state of their own here. */
export function useTeamCards(teamId: string) {
  const { api } = useAuth();
  const q = useInfiniteQuery(teamCardsOptions(api, teamId));
  const cards = useMemo(() => q.data?.pages.flatMap((p) => p.cards) ?? [], [q.data]);
  return { ...q, cards };
}
