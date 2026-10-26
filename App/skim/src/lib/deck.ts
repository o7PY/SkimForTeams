import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';

import { useAuth } from './auth';
import { asSwiped, dropFromList, flatten, hiddenByPendingOps, insertCard, prependToList, removeCard, stateOp, type DeckData } from './deck-cache';
import { useOutbox } from './outbox-runtime';
import { queryKeys } from './query';
import { deckOptions } from './query-options';
import type { Card } from './types';

/** The deck: pages from /deck, flattened, minus cards the student has already acted on whose change is still in the outbox. */
export function useDeck() {
  const { api } = useAuth();
  const { ops } = useOutbox();
  const q = useInfiniteQuery(deckOptions(api));
  const hidden = useMemo(() => hiddenByPendingOps(ops), [ops]);
  const cards = useMemo(() => flatten(q.data as DeckData | undefined).filter((c) => !hidden.has(c.id)), [q.data, hidden]);
  return { ...q, cards };
}

/** Swipe actions: the screen changes at once (optimistic), the outbox makes the server agree, and a refetch after delivery settles it. */
export function useDeckActions() {
  const qc = useQueryClient();
  const { enqueue } = useOutbox();
  const take = useCallback((card: Card) => qc.setQueryData<DeckData>(queryKeys.deck, (d) => removeCard(d, card.id)), [qc]);
  return useMemo(
    () => ({
      // A swiped card is in History straight away, also offline (the History tab does not wait for the server).
      dismiss: async (card: Card) => {
        take(card);
        qc.setQueryData<{ cards: Card[] }>(queryKeys.history, (d) => prependToList(d, asSwiped(card, 'dismissed', new Date())));
        await enqueue(stateOp(card.id, 'dismiss'));
      },
      open: async (card: Card) => {
        take(card);
        qc.setQueryData<{ cards: Card[] }>(queryKeys.history, (d) => prependToList(d, asSwiped(card, 'opened', new Date())));
        await enqueue(stateOp(card.id, 'open'));
      },
      undo: async (card: Card) => {
        qc.setQueryData<DeckData>(queryKeys.deck, (d) => insertCard(d, card, 0));
        qc.setQueryData<{ cards: Card[] }>(queryKeys.history, (d) => dropFromList(d, card.id));
        await enqueue(stateOp(card.id, 'undo'));
      },
    }),
    [take, qc, enqueue],
  );
}
