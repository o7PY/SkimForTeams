// Optimistic edits of the cached deck (an infinite query): a swipe removes the card at once, undo puts it back at the top. The
// outbox then makes the server agree, and a refetch after delivery replaces all of this with what the server holds.
import type { Card, DeckPage } from './types';

export type DeckData = { pages: DeckPage[]; pageParams: unknown[] };

export function flatten(data: DeckData | undefined): Card[] {
  const seen = new Set<number>();
  const out: Card[] = [];
  for (const p of data?.pages ?? []) {
    for (const c of p.cards) {
      if (!seen.has(c.id)) { // pages can overlap when the deck changed between two fetches
        seen.add(c.id);
        out.push(c);
      }
    }
  }
  return out;
}

export function removeCard(data: DeckData | undefined, id: number): DeckData | undefined {
  if (!data) return data;
  return {
    ...data,
    pages: data.pages.map((p) => (p.cards.some((c) => c.id === id) ? { ...p, cards: p.cards.filter((c) => c.id !== id), total: Math.max(0, p.total - 1) } : p)),
  };
}

/** Put a card back at `index` of the whole deck. The pages collapse into one, keeping the last cursor, so paging carries on. */
export function insertCard(data: DeckData | undefined, card: Card, index: number): DeckData | undefined {
  if (!data || data.pages.length === 0) return data;
  const cards = flatten(data).filter((c) => c.id !== card.id);
  cards.splice(Math.max(0, Math.min(index, cards.length)), 0, card);
  const last = data.pages[data.pages.length - 1];
  return { pages: [{ cards, next_cursor: last.next_cursor, total: last.total + 1 }], pageParams: [data.pageParams[0] ?? null] };
}

// ---------------------------------------------------------------- pending outbox operations
type OpLike = { key: string; body?: unknown };

/** Cards the student already acted on whose operation has not reached the server yet. They stay out of the deck, so a refetch
 *  that races the outbox cannot bring a swiped card back. */
export function hiddenByPendingOps(ops: OpLike[]): Set<number> {
  const hidden = new Set<number>();
  for (const op of ops) {
    const m = /^state:(\d+)$/.exec(op.key);
    const action = (op.body as { action?: string } | undefined)?.action;
    if (m && (action === 'dismiss' || action === 'open' || action === 'snooze')) hidden.add(Number(m[1]));
  }
  return hidden;
}

export const stateOp = (id: number, action: 'dismiss' | 'open' | 'undo' | 'restore' | 'snooze', until?: string) => ({
  key: `state:${id}`,
  method: 'POST' as const,
  path: `/items/${id}/state`,
  body: until ? { action, until } : { action },
});

// ---------------------------------------------------------------- history and starred lists
type Listed = { id: number };
type ListData<T extends Listed> = { cards: T[] } | undefined;

export function dropFromList<T extends Listed>(data: ListData<T>, id: number): ListData<T> {
  return data ? { ...data, cards: data.cards.filter((c) => c.id !== id) } : data;
}

/** History cards the student already put back whose "restore" is still in the outbox: they leave the list at once and stay gone
 *  even if a refetch races ahead of delivery. */
export function restoredByPendingOps(ops: OpLike[]): Set<number> {
  const out = new Set<number>();
  for (const op of ops) {
    const m = /^state:(\d+)$/.exec(op.key);
    if (m && (op.body as { action?: string } | undefined)?.action === 'restore') out.add(Number(m[1]));
  }
  return out;
}

/** Stars the student removed whose delete is still in the outbox. */
export function unstarredByPendingOps(ops: (OpLike & { method?: string })[]): Set<number> {
  const out = new Set<number>();
  for (const op of ops) {
    const m = /^star:(\d+)$/.exec(op.key);
    if (m && op.method === 'DELETE') out.add(Number(m[1]));
  }
  return out;
}

// ---------------------------------------------------------------- keeping the other tabs right while offline
/** Put a card at the top of a list (replacing it if it is already there), the way History and Starred show newest first. */
export function prependToList<T extends Listed>(data: { cards: T[] } | undefined, card: T): { cards: T[] } {
  return { ...(data ?? {}), cards: [card, ...(data?.cards ?? []).filter((c) => c.id !== card.id)] };
}

/** How a swiped card appears in History right after the swipe (before the server has been asked). */
export const asSwiped = (card: Card, state: 'dismissed' | 'opened', at: Date): Card => ({ ...card, swiped: { state, at: at.toISOString() } });

/** How a starred card appears in Starred right after the tap. */
export const asStarredEntry = (card: Card, at: Date): Card => ({ ...card, starred_at: at.toISOString() });

/** A card coming out of History back into the deck is an ordinary deck card again. */
export const withoutSwiped = (card: Card): Card => {
  const { swiped: _swiped, ...rest } = card;
  return rest;
};
