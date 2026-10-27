// History and Starred: lists of cards with one action each (put back / remove star). Same pattern as the deck: change the screen at
// once, queue the change in the outbox, and let a refetch after delivery settle it.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';

import { useAuth } from './auth';
import { dropFromList, insertCard, restoredByPendingOps, stateOp, unstarredByPendingOps, withoutSwiped, type DeckData } from './deck-cache';
import { useOutbox } from './outbox-runtime';
import { queryKeys } from './query';
import { historyOptions, starredOptions } from './query-options';
import type { Card } from './types';

type List = { cards: Card[] };

/** While changes are still waiting to be sent, a refresh of these lists would show the server's OLD view and wipe what the student
 *  just did (a card swiped or starred offline). So they hold still until the outbox is empty; its delivery refetches everything. */
export const calm = (pending: number) =>
  pending === 0 ? {} : { refetchOnMount: false as const, refetchOnReconnect: false as const, refetchOnWindowFocus: false as const };

export function useHistory() {
  const { api } = useAuth();
  const { ops, pending } = useOutbox();
  const q = useQuery({ ...historyOptions(api), ...calm(pending) });
  const gone = useMemo(() => restoredByPendingOps(ops), [ops]);
  const cards = useMemo(() => (q.data?.cards ?? []).filter((c) => !gone.has(c.id)), [q.data, gone]);
  return { ...q, cards };
}

export function useStarred() {
  const { api } = useAuth();
  const { ops, pending } = useOutbox();
  const q = useQuery({ ...starredOptions(api), ...calm(pending) });
  const gone = useMemo(() => unstarredByPendingOps(ops), [ops]);
  const cards = useMemo(() => (q.data?.cards ?? []).filter((c) => !gone.has(c.id)), [q.data, gone]);
  return { ...q, cards };
}

export function useListActions() {
  const qc = useQueryClient();
  const { enqueue } = useOutbox();
  return useMemo(
    () => ({
      /** back into the deck, on top (P4-D-113) */
      async putBack(card: Card) {
        qc.setQueryData<List>(queryKeys.history, (d) => dropFromList(d, card.id));
        qc.setQueryData<DeckData>(queryKeys.deck, (d) => insertCard(d, withoutSwiped(card), 0)); // it is in the deck at once, also offline
        await enqueue(stateOp(card.id, 'restore'));
      },
      async unstar(card: Card) {
        qc.setQueryData<List>(queryKeys.starred, (d) => dropFromList(d, card.id));
        await enqueue({ key: `star:${card.id}`, method: 'DELETE', path: `/items/${card.id}/star` });
      },
    }),
    [qc, enqueue],
  );
}
