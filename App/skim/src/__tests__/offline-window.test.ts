import { prependToList, asStarredEntry, asSwiped, withoutSwiped } from '@/lib/deck-cache';
import { evictIds, keepIds, MAX_STARRED_KEPT, pruneForSize, runPool, waitUntilQuiet, WINDOW } from '@/lib/offline-window';
import { keyboardOverlap, sheetLayout } from '@/lib/sheet-layout';

import { messageCard } from './fixtures';

const at = (day: number, hour = 12) => `2026-10-${String(day).padStart(2, '0')}T${String(hour).padStart(2, '0')}:00:00+05:30`;
const card = (id: number, day: number, hour = 12) => ({ id, occurred_at: at(day, hour) });

describe('keepIds: the last 50 cards stay readable offline', () => {
  test('the 50 newest of deck + history are kept; the 51st oldest drops out when a newer card arrives', () => {
    const cards = Array.from({ length: 50 }, (_, i) => ({ id: i + 1, occurred_at: new Date(Date.UTC(2026, 9, 1, i)).toISOString() }));
    expect(keepIds(cards, [], [])).toHaveLength(WINDOW);
    expect(keepIds(cards, [], [])).toContain(1);
    const newer = { id: 99, occurred_at: new Date(Date.UTC(2026, 9, 5)).toISOString() };
    const keep = keepIds([newer, ...cards], [], []);
    expect(keep).toHaveLength(WINDOW);
    expect(keep[0]).toBe(99);
    expect(keep).not.toContain(1);   // the oldest is the one that goes
    expect(keep).toContain(2);
  });
  test('unread and swiped cards share one window, newest first, each once', () => {
    const keep = keepIds([card(1, 20), card(2, 24)], [card(3, 22), card(2, 24)], []);
    expect(keep).toEqual([2, 3, 1]);
  });
  test('equal times are ordered by id so the choice is stable', () => {
    expect(keepIds([card(1, 20), card(5, 20), card(3, 20)], [], [])).toEqual([5, 3, 1]);
  });
  test('starred cards are always kept, even when they are far older than the window', () => {
    const many = Array.from({ length: 60 }, (_, i) => ({ id: 100 + i, occurred_at: new Date(Date.UTC(2026, 9, 10, i)).toISOString() }));
    const keep = keepIds(many, [], [card(7, 1), card(8, 2)]);
    expect(keep).toHaveLength(WINDOW + 2);
    expect(keep).toEqual(expect.arrayContaining([7, 8]));
  });
  test('a starred card that is also in the window is not counted twice; the number of extra stars is capped', () => {
    expect(keepIds([card(1, 20)], [], [card(1, 20)])).toEqual([1]);
    const stars = Array.from({ length: MAX_STARRED_KEPT + 30 }, (_, i) => card(1000 + i, 1, 1 + (i % 20)));
    expect(keepIds([], [], stars)).toHaveLength(MAX_STARRED_KEPT);
  });
  test('garbage times do not crash it', () => {
    expect(keepIds([{ id: 1, occurred_at: 'nope' }, card(2, 20)], [], [])).toEqual([2, 1]);
    expect(keepIds([], [], [])).toEqual([]);
  });
  test('a smaller window can be asked for', () => {
    expect(keepIds([card(1, 1), card(2, 2), card(3, 3)], [], [], 2)).toEqual([3, 2]);
  });
});

test('evictIds names what was cached but is no longer wanted', () => {
  expect(evictIds([1, 2, 3, 4], [3, 4, 5])).toEqual([1, 2]);
  expect(evictIds([], [1])).toEqual([]);
  expect(evictIds([1], [])).toEqual([1]);
});

describe('runPool', () => {
  test('never runs more than n at once, and does every item', async () => {
    let active = 0, peak = 0;
    const done: number[] = [];
    await runPool([1, 2, 3, 4, 5, 6, 7, 8], 3, async (x) => {
      active++; peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 5));
      active--; done.push(x);
    });
    expect(peak).toBe(3);
    expect(done.sort()).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
  });
  test('one failing item does not stop the others', async () => {
    const done: number[] = [];
    await runPool([1, 2, 3, 4], 2, async (x) => { if (x === 2) throw new Error('x'); done.push(x); });
    expect(done.sort()).toEqual([1, 3, 4]);
  });
  test('an empty list and a pool larger than the list are fine', async () => {
    await runPool([], 3, async () => { throw new Error('never'); });
    const done: number[] = [];
    await runPool([1], 10, async (x) => void done.push(x));
    expect(done).toEqual([1]);
  });
});

describe('pruneForSize keeps the saved cache readable', () => {
  const q = (key: unknown[], updated: number, size = 100) => ({ queryKey: key, state: { dataUpdatedAt: updated, data: 'x'.repeat(size) } });
  const client = (queries: ReturnType<typeof q>[]) => ({ timestamp: 1, buster: 'v1', clientState: { mutations: [], queries } });

  test('under the limit nothing changes (same object)', () => {
    const c = client([q(['item', '1'], 1), q(['deck'], 2)]);
    expect(pruneForSize(c, 10_000)).toBe(c);
  });
  test('over the limit the OLDEST opened-card details go first, one at a time, and only as many as needed', () => {
    const c = client([q(['deck'], 9, 500), q(['item', '1'], 1, 300), q(['item', '2'], 2, 300), q(['item', '3'], 3, 300), q(['history'], 9, 500)]);
    const out = pruneForSize(c, JSON.stringify(c).length - 400);
    const kept = out.clientState.queries.map((x) => x.queryKey.join('/'));
    expect(kept).toEqual(['deck', 'item/3', 'history']);
    expect(JSON.stringify(out).length).toBeLessThanOrEqual(JSON.stringify(c).length - 400);
  });
  test('lists, settings and everything that is not an opened card are never dropped, even if that is not enough', () => {
    const c = client([q(['deck'], 1, 5000), q(['starred'], 1, 5000), q(['me'], 1, 100), q(['item', '1'], 1, 100)]);
    const out = pruneForSize(c, 100);
    expect(out.clientState.queries.map((x) => x.queryKey[0])).toEqual(['deck', 'starred', 'me']);
  });
  test('the input is not changed', () => {
    const c = client([q(['item', '1'], 1, 5000), q(['item', '2'], 2, 5000)]);
    pruneForSize(c, 1000);
    expect(c.clientState.queries).toHaveLength(2);
  });
});

describe('team card lists in the saved cache', () => {
  const list = (team: string, pages: number, updated: number, size = 100) => ({
    queryKey: ['team-cards', team], state: { dataUpdatedAt: updated, data: { pages: Array.from({ length: pages }, (_, i) => ({ cards: ['x'.repeat(size)], next_cursor: `c${i}` })), pageParams: Array.from({ length: pages }, (_, i) => (i ? `c${i - 1}` : null)) } },
  });
  const q = (key: unknown[], updated: number, size = 100) => ({ queryKey: key, state: { dataUpdatedAt: updated, data: 'x'.repeat(size) } });
  const client = (queries: unknown[]) => ({ timestamp: 1, buster: 'v1', clientState: { mutations: [], queries } }) as never;
  type Out = { clientState: { queries: { queryKey: unknown[]; state: { data?: { pages: unknown[]; pageParams: unknown[] } } }[] } };

  test('only the first page of a team is saved, with its own page parameter', () => {
    const out = pruneForSize(client([list('T1', 4, 1)]), 1_000_000) as Out;
    const d = out.clientState.queries[0].state.data!;
    expect(d.pages).toHaveLength(1);
    expect(d.pageParams).toEqual([null]);
  });
  test('a list that is already one page is left alone (same object)', () => {
    const c = client([list('T1', 1, 1), q(['deck'], 1)]);
    expect(pruneForSize(c, 1_000_000)).toBe(c);
  });
  test('the input is not changed by trimming', () => {
    const c = client([list('T1', 3, 1)]) as Out;
    pruneForSize(c as never, 1_000_000);
    expect(c.clientState.queries[0].state.data!.pages).toHaveLength(3);
  });
  test('over the limit the oldest team lists go BEFORE any opened-card detail, and the deck is never touched', () => {
    const c = client([q(['deck'], 9, 500), list('T1', 1, 1, 400), list('T2', 1, 5, 400), q(['item', '1'], 1, 300), q(['item', '2'], 2, 300)]);
    const total = JSON.stringify(c).length;
    const out = pruneForSize(c, total - 500) as Out;
    const kept = out.clientState.queries.map((x) => (x.queryKey as string[]).join('/'));
    expect(kept).toEqual(['deck', 'team-cards/T2', 'item/1', 'item/2']);   // only the oldest list had to go
    const more = pruneForSize(c, total - 1200) as Out;
    expect(more.clientState.queries.map((x) => (x.queryKey as string[]).join('/'))).toEqual(['deck', 'item/2']);
  });
});

describe('waitUntilQuiet', () => {
  test('returns at once when already quiet', async () => {
    const t = Date.now();
    await waitUntilQuiet(() => true, 5000);
    expect(Date.now() - t).toBeLessThan(100);
  });
  test('waits until it turns quiet', async () => {
    let busy = true;
    setTimeout(() => { busy = false; }, 60);
    await waitUntilQuiet(() => !busy, 5000, 10);
    expect(busy).toBe(false);
  });
  test('gives up after the limit, so a busy screen can delay the loader but never stop it', async () => {
    const t = Date.now();
    await waitUntilQuiet(() => false, 80, 10);
    expect(Date.now() - t).toBeGreaterThanOrEqual(70);
    expect(Date.now() - t).toBeLessThan(500);
  });
});

describe('keyboard and the bottom sheet', () => {
  test('overlap: how far the keyboard covers the sheet window', () => {
    expect(keyboardOverlap(2400, 1500)).toBe(900);   // window did not shrink: the keyboard covers 900 px
    expect(keyboardOverlap(1500, 1500)).toBe(0);     // the window shrank above the keyboard already: nothing to add
    expect(keyboardOverlap(1400, 1500)).toBe(0);
    expect(keyboardOverlap(2400, null)).toBe(0);     // no keyboard
    expect(keyboardOverlap(2400, NaN)).toBe(0);
    expect(keyboardOverlap(NaN, 1500)).toBe(0);
  });
  test('the panel is lifted by the overlap and stays within the room that is left', () => {
    expect(sheetLayout(2400, 0)).toEqual({ paddingBottom: 0, maxHeight: Math.floor(2400 * 0.92) });
    const l = sheetLayout(2400, 900);
    expect(l.paddingBottom).toBe(900);
    expect(l.maxHeight).toBe(Math.floor(1500 * 0.92));
    expect(l.paddingBottom + l.maxHeight).toBeLessThan(2400);
  });
  test('absurd values cannot push the sheet off screen or make it too small to use', () => {
    expect(sheetLayout(2400, 99999).paddingBottom).toBeLessThanOrEqual(2400 * 0.8);
    expect(sheetLayout(400, 390).maxHeight).toBeGreaterThanOrEqual(220);
    expect(sheetLayout(2400, -50).paddingBottom).toBe(0);
  });
});

describe('list edits that keep History and Starred right before the server is reached', () => {
  test('prependToList puts the card on top once, even if it was already somewhere in the list', () => {
    const a = messageCard({ id: 1 }), b = messageCard({ id: 2 }), c = messageCard({ id: 3 });
    expect(prependToList({ cards: [a, b] }, c).cards.map((x) => x.id)).toEqual([3, 1, 2]);
    expect(prependToList({ cards: [a, b, c] }, c).cards.map((x) => x.id)).toEqual([3, 1, 2]);
    expect(prependToList(undefined, a).cards.map((x) => x.id)).toEqual([1]);
  });
  test('a swiped card carries its state and time; a card put back loses them', () => {
    const swiped = asSwiped(messageCard({ id: 4 }), 'dismissed', new Date('2026-10-27T01:00:00Z'));
    expect(swiped.swiped).toEqual({ state: 'dismissed', at: '2026-10-27T01:00:00.000Z' });
    expect(withoutSwiped(swiped)).not.toHaveProperty('swiped');
    expect(withoutSwiped(messageCard())).not.toHaveProperty('swiped');
  });
  test('a starred entry records when it was starred', () => {
    expect(asStarredEntry(messageCard({ id: 5 }), new Date('2026-10-27T01:00:00Z')).starred_at).toBe('2026-10-27T01:00:00.000Z');
  });
});
