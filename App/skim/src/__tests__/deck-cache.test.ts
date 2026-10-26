import { flatten, hiddenByPendingOps, insertCard, removeCard, stateOp, type DeckData } from '@/lib/deck-cache';

import { cardsWithIds } from './fixtures';

const data = (...pages: number[][]): DeckData => ({
  pages: pages.map((ids, i) => ({ cards: cardsWithIds(...ids), next_cursor: i < pages.length - 1 ? `c${i}` : null, total: pages.flat().length })),
  pageParams: pages.map((_, i) => (i === 0 ? null : `c${i - 1}`)),
});
const ids = (d: DeckData | undefined) => flatten(d).map((c) => c.id);

test('flatten joins pages in order and drops cards that appear twice (pages can overlap)', () => {
  expect(ids(data([1, 2, 3], [3, 4, 5]))).toEqual([1, 2, 3, 4, 5]);
  expect(flatten(undefined)).toEqual([]);
});

test('removeCard takes a card out of whichever page holds it and keeps the cursors', () => {
  const d = removeCard(data([1, 2, 3], [4, 5]), 4)!;
  expect(ids(d)).toEqual([1, 2, 3, 5]);
  expect(d.pages.map((p) => p.next_cursor)).toEqual(['c0', null]);
  expect(d.pages[1].total).toBe(4);
  expect(ids(removeCard(data([1, 2]), 99))).toEqual([1, 2]); // unknown card: unchanged
  expect(removeCard(undefined, 1)).toBeUndefined();
});

test('removeCard does not change its input', () => {
  const d = data([1, 2, 3]);
  removeCard(d, 2);
  expect(ids(d)).toEqual([1, 2, 3]);
});

test('undo: remove then insert at 0 restores the original order', () => {
  const d = data([1, 2, 3], [4, 5]);
  const back = insertCard(removeCard(d, 1), flatten(d)[0], 0)!;
  expect(ids(back)).toEqual([1, 2, 3, 4, 5]);
  expect(back.pages).toHaveLength(1);
  expect(back.pages[0].next_cursor).toBeNull(); // the last page's cursor survives, so paging still works
});

test('insertCard clamps its index, never duplicates, and keeps the last cursor', () => {
  const d = data([1, 2], [3, 4]);
  const card = flatten(d)[1];
  expect(ids(insertCard(d, card, 99))).toEqual([1, 3, 4, 2]);
  expect(ids(insertCard(d, card, -5))).toEqual([2, 1, 3, 4]);
  expect(ids(insertCard(d, card, 0))).toEqual([2, 1, 3, 4]);
  const more = data([1, 2], [3, 4]);
  more.pages[1].next_cursor = 'more';
  expect(insertCard(more, card, 0)!.pages[0].next_cursor).toBe('more');
  expect(insertCard(undefined, card, 0)).toBeUndefined();
});

test('cards the student already acted on stay hidden while the operation is still on its way', () => {
  const ops = [
    { key: 'state:5', body: { action: 'dismiss' } }, { key: 'state:6', body: { action: 'open' } }, { key: 'state:7', body: { action: 'snooze', until: 'x' } },
    { key: 'state:8', body: { action: 'undo' } }, { key: 'state:9', body: { action: 'restore' } }, { key: 'star:5', body: { temp: null } },
    { key: 'settings', body: { history_limit: 80 } }, { key: 'state:abc', body: { action: 'dismiss' } }, { key: 'state:10' },
  ];
  expect([...hiddenByPendingOps(ops)].sort()).toEqual([5, 6, 7]);
});

test('stateOp builds the outbox operation, keyed by the card so the newest action wins', () => {
  expect(stateOp(4, 'dismiss')).toEqual({ key: 'state:4', method: 'POST', path: '/items/4/state', body: { action: 'dismiss' } });
  expect(stateOp(4, 'snooze', '2026-10-27T09:00:00+05:30').body).toEqual({ action: 'snooze', until: '2026-10-27T09:00:00+05:30' });
});
