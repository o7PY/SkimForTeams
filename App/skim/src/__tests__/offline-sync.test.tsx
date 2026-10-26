import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create } from 'react-test-renderer';

import { flatten, type DeckData } from '@/lib/deck-cache';
import { OfflineSync } from '@/lib/offline-runtime';
import { queryKeys } from '@/lib/query';
import type { Card } from '@/lib/types';

import { messageCard } from './fixtures';

// ---- a fake server and a fake phone
jest.mock('@react-native-async-storage/async-storage', () => jest.requireActual('@react-native-async-storage/async-storage/jest/async-storage-mock'));
const mockGet = jest.fn();
let mockPending = 0;
let mockOnline = true;
jest.mock('@/lib/auth', () => ({ useAuth: () => ({ api: { get: (p: string) => mockGet(p) }, session: { user: { id: 'u1' } }, status: 'signedIn' }) }));
jest.mock('@/lib/outbox-runtime', () => ({ useOutbox: () => ({ pending: mockPending }) }));
jest.mock('expo-network', () => ({
  getNetworkStateAsync: async () => ({ isConnected: mockOnline }),
  addNetworkStateListener: () => ({ remove: jest.fn() }),
}));

const at = (n: number) => new Date(Date.UTC(2026, 9, 1, 0, n)).toISOString(); // card n happened n minutes into Oct 1: bigger = newer
const card = (id: number, over: Partial<Card> = {}): Card => messageCard({ id, occurred_at: at(id), ...over });
const detail = (id: number): Card => ({ ...card(id), recommended_reminders: [], event_history: [], folded: [] });

function serve(opts: { deck?: number[]; history?: number[]; starred?: number[] }) {
  mockGet.mockImplementation(async (path: string) => {
    if (path.startsWith('/deck')) return { cards: (opts.deck ?? []).map((i) => card(i)), next_cursor: null, total: (opts.deck ?? []).length };
    if (path === '/history') return { cards: (opts.history ?? []).map((i) => card(i)) };
    if (path === '/starred') return { cards: (opts.starred ?? []).map((i) => card(i)) };
    if (path.startsWith('/reminders')) return { reminders: [], etag: 'e1' };
    if (path === '/subscriptions') return { teams: [] };
    if (path === '/me') return { id: 'u1', email: null, name: null, created_at: at(0), settings: {} };
    const m = /^\/items\/(\d+)$/.exec(path);
    if (m) return detail(Number(m[1]));
    throw new Error(`unexpected ${path}`);
  });
}

/** Wait until nothing is being fetched for a few consecutive checks, i.e. the loader has really finished (not just "a while"). */
async function settle(qc: QueryClient) {
  let quiet = 0;
  for (let i = 0; i < 400 && quiet < 6; i++) {
    await new Promise((r) => setTimeout(r, 5));
    quiet = qc.isFetching() === 0 ? quiet + 1 : 0;
  }
}

async function mount(existing?: QueryClient) {
  const qc = existing ?? new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  // Effects only run when the act() block ends, so start the component there, THEN wait for the loader outside it.
  await act(async () => {
    create(<QueryClientProvider client={qc}><OfflineSync /></QueryClientProvider>);
  });
  await settle(qc);
  return qc;
}
/** ids of cards whose full detail is actually saved (an entry left by a failed load has no data and does not count) */
const itemIds = (qc: QueryClient) => qc.getQueryCache().findAll({ queryKey: queryKeys.item }).filter((q) => q.state.data !== undefined).map((q) => Number(q.queryKey[1])).sort((a, b) => a - b);
const range = (a: number, b: number) => Array.from({ length: b - a + 1 }, (_, i) => a + i);
const calls = (prefix: string) => mockGet.mock.calls.filter(([p]) => String(p).startsWith(prefix)).length;

beforeEach(() => {
  mockGet.mockReset(); mockPending = 0; mockOnline = true;
  jest.spyOn(console, 'log').mockImplementation(() => undefined); // the loader prints a dev line when it finishes
});

test('on sign-in it loads every list AND the full detail of the cards, before any screen is opened', async () => {
  serve({ deck: range(1, 20), history: range(21, 25), starred: [3] });
  const qc = await mount();
  expect(flatten(qc.getQueryData<DeckData>(queryKeys.deck)).length).toBe(20);
  expect(qc.getQueryData<{ cards: Card[] }>(queryKeys.history)?.cards).toHaveLength(5);
  expect(qc.getQueryData<{ cards: Card[] }>(queryKeys.starred)?.cards).toHaveLength(1);
  expect(qc.getQueryData(queryKeys.reminders)).toBeTruthy();
  expect(qc.getQueryData(queryKeys.subscriptions)).toEqual([]);
  expect(qc.getQueryData(queryKeys.me)).toBeTruthy();
  expect(itemIds(qc)).toEqual(range(1, 25));       // all 25 cards readable in full offline
  expect(qc.getQueryData<Card>(['item', '7'])?.recommended_reminders).toEqual([]); // it is the DETAIL payload, not the list one
});

test('the first page asked for holds 50 cards', async () => {
  serve({ deck: range(1, 5) });
  await mount();
  expect(mockGet.mock.calls.map(([p]) => p)).toContain('/deck?limit=50');
});

test('only the 50 newest cards are kept in full; the 51st oldest is not even loaded', async () => {
  serve({ deck: range(1, 60) });
  const qc = await mount();
  expect(itemIds(qc)).toEqual(range(11, 60));      // 50 newest
  expect(calls('/items/')).toBe(50);
});

test('when a newer card arrives, the oldest saved detail is dropped (the 51st pushes out the 1st)', async () => {
  serve({ deck: range(2, 51) });                   // 50 cards: 2..51
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  qc.setQueryData(['item', '1'], detail(1));        // an older card saved earlier
  await mount(qc);
  expect(itemIds(qc)).toEqual(range(2, 51));
  expect(qc.getQueryData(['item', '1'])).toBeUndefined();
});

test('starred cards are kept in full even when they are far older than the window', async () => {
  serve({ deck: range(100, 160), starred: [1, 2] });
  const qc = await mount();
  expect(itemIds(qc)).toEqual([1, 2, ...range(111, 160)]);
});

test('read cards (History) share the window with unread ones', async () => {
  serve({ deck: range(1, 30), history: range(31, 80) });
  const qc = await mount();
  expect(itemIds(qc)).toEqual(range(31, 80));      // the 50 newest overall, whichever list they are in
});

test('offline: nothing is requested and nothing already saved is lost', async () => {
  serve({ deck: range(1, 5) });
  mockOnline = false;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  qc.setQueryData(['item', '9'], detail(9));
  await mount(qc);
  expect(mockGet).not.toHaveBeenCalled();
  expect(qc.getQueryData(['item', '9'])).toBeTruthy();
});

test('while changes are waiting to be sent the lists are NOT refreshed (they would erase what the student just did), but details still load', async () => {
  serve({ deck: range(1, 6) });
  mockPending = 2;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  qc.setQueryData(queryKeys.deck, { pages: [{ cards: range(1, 6).map((i) => card(i)), next_cursor: null, total: 6 }], pageParams: [null] });
  await mount(qc);
  expect(calls('/deck')).toBe(0);
  expect(calls('/history')).toBe(0);
  expect(calls('/starred')).toBe(0);
  expect(itemIds(qc)).toEqual(range(1, 6));
});

test('one card failing to load does not stop the others', async () => {
  serve({ deck: range(1, 6) });
  const base = mockGet.getMockImplementation()!;
  mockGet.mockImplementation(async (p: string) => { if (p === '/items/3') throw new Error('boom'); return base(p); });
  const qc = await mount();
  expect(itemIds(qc)).toEqual([1, 2, 4, 5, 6]);
});

test('details at most 3 at a time', async () => {
  serve({ deck: range(1, 12) });
  let active = 0, peak = 0;
  const base = mockGet.getMockImplementation()!;
  mockGet.mockImplementation(async (p: string) => {
    if (!p.startsWith('/items/')) return base(p);
    active++; peak = Math.max(peak, active);
    await new Promise((r) => setTimeout(r, 8));
    active--;
    return base(p);
  });
  await mount();
  expect(peak).toBe(3);
});
