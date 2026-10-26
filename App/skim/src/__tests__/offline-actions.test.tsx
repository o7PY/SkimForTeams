import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestInstance } from 'react-test-renderer';
import { Keyboard } from 'react-native';

import { useDeckActions } from '@/lib/deck';
import { useItemActions } from '@/lib/item-actions';
import { useListActions } from '@/lib/lists';
import { queryKeys } from '@/lib/query';
import type { Card } from '@/lib/types';
import { Sheet } from '@/ui/sheet';

import { messageCard } from './fixtures';

jest.mock('@react-native-async-storage/async-storage', () => jest.requireActual('@react-native-async-storage/async-storage/jest/async-storage-mock'));
const enqueued: unknown[] = [];
jest.mock('@/lib/outbox-runtime', () => ({ useOutbox: () => ({ enqueue: async (op: unknown) => void enqueued.push(op), pending: 0, ops: [] }) }));
jest.mock('@/lib/auth', () => ({ useAuth: () => ({ api: {}, session: { user: { id: 'u1' } } }) }));
jest.mock('expo-router', () => ({ router: { back: jest.fn(), canGoBack: () => true, replace: jest.fn() } }));
jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});
jest.mock('react-native-safe-area-context', () => ({ SafeAreaView: jest.requireActual('react-native').View }));

type Hooks = { item: ReturnType<typeof useItemActions>; deck: ReturnType<typeof useDeckActions>; lists: ReturnType<typeof useListActions> };

function setup(card: Card) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  qc.setQueryData(['item', String(card.id)], card);
  qc.setQueryData(queryKeys.deck, { pages: [{ cards: [card, messageCard({ id: 2 })], next_cursor: null, total: 2 }], pageParams: [null] });
  qc.setQueryData(queryKeys.history, { cards: [] });
  qc.setQueryData(queryKeys.starred, { cards: [] });
  const out: { current: Hooks | null } = { current: null };
  function Probe() {
    out.current = { item: useItemActions(card), deck: useDeckActions(), lists: useListActions() };
    return null;
  }
  act(() => { create(<QueryClientProvider client={qc}><Probe /></QueryClientProvider>); });
  return { qc, h: out as { current: Hooks } };
}
const ids = (qc: QueryClient, key: readonly string[]) => (qc.getQueryData<{ cards: Card[] }>(key)?.cards ?? []).map((c) => c.id);

beforeEach(() => { enqueued.length = 0; });

describe('the bug: starred offline did not appear in the Starred tab', () => {
  test('starring a card puts it in the Starred list at once, newest first, marked as starred', async () => {
    const { qc, h } = setup(messageCard({ id: 1 }));
    qc.setQueryData(queryKeys.starred, { cards: [messageCard({ id: 9 })] });
    await act(async () => { await h.current.item!.star(null); });
    expect(ids(qc, queryKeys.starred)).toEqual([1, 9]);
    const entry = qc.getQueryData<{ cards: Card[] }>(queryKeys.starred)!.cards[0];
    expect(entry.user.starred).toBe(true);
    expect(entry.starred_at).toBeTruthy();
    expect(enqueued).toEqual([{ key: 'star:1', method: 'PUT', path: '/items/1/star', body: { temp: null } }]); // and it is queued for the server
  });
  test('a temporary star carries its expiry into the list', async () => {
    const { qc, h } = setup(messageCard({ id: 1 }));
    await act(async () => { await h.current.item!.star('3d'); });
    expect(qc.getQueryData<{ cards: Card[] }>(queryKeys.starred)!.cards[0].user.temp_star_expires_at).toBeTruthy();
  });
  test('starring twice does not list the card twice; unstarring removes it from the list', async () => {
    const { qc, h } = setup(messageCard({ id: 1 }));
    await act(async () => { await h.current.item!.star(null); await h.current.item!.star('1d'); });
    expect(ids(qc, queryKeys.starred)).toEqual([1]);
    await act(async () => { await h.current.item!.unstar(); });
    expect(ids(qc, queryKeys.starred)).toEqual([]);
  });
  test('removing a star from the Starred tab itself also works offline', async () => {
    const c = messageCard({ id: 4 });
    const { qc, h } = setup(c);
    qc.setQueryData(queryKeys.starred, { cards: [c] });
    await act(async () => { await h.current.lists.unstar(c); });
    expect(ids(qc, queryKeys.starred)).toEqual([]);
    expect(enqueued).toEqual([{ key: 'star:4', method: 'DELETE', path: '/items/4/star' }]);
  });
  test('the opened card itself also shows the star (as before)', async () => {
    const { qc, h } = setup(messageCard({ id: 1 }));
    await act(async () => { await h.current.item!.star(null); });
    expect(qc.getQueryData<Card>(['item', '1'])!.user.starred).toBe(true);
  });
});

describe('the same for swipes: History is right before the server is reached', () => {
  test('a card swiped away is in History straight away, with how it was swiped, and gone from the deck', async () => {
    const c = messageCard({ id: 1 });
    const { qc, h } = setup(c);
    await act(async () => { await h.current.deck.dismiss(c); });
    expect(ids(qc, queryKeys.history)).toEqual([1]);
    expect(qc.getQueryData<{ cards: Card[] }>(queryKeys.history)!.cards[0].swiped?.state).toBe('dismissed');
    const deck = qc.getQueryData<{ pages: { cards: Card[] }[] }>(queryKeys.deck)!;
    expect(deck.pages[0].cards.map((x) => x.id)).toEqual([2]);
    await act(async () => { await h.current.deck.open(messageCard({ id: 2 })); });
    expect(ids(qc, queryKeys.history)).toEqual([2, 1]);
    expect(qc.getQueryData<{ cards: Card[] }>(queryKeys.history)!.cards[0].swiped?.state).toBe('opened');
  });
  test('Undo takes it out of History and back to the top of the deck', async () => {
    const c = messageCard({ id: 1 });
    const { qc, h } = setup(c);
    await act(async () => { await h.current.deck.dismiss(c); await h.current.deck.undo(c); });
    expect(ids(qc, queryKeys.history)).toEqual([]);
    expect(qc.getQueryData<{ pages: { cards: Card[] }[] }>(queryKeys.deck)!.pages[0].cards.map((x) => x.id)).toEqual([1, 2]);
  });
  test('"Put back" from History puts it on top of the deck at once, as an ordinary card', async () => {
    const c = messageCard({ id: 5, swiped: { state: 'dismissed', at: '2026-10-27T01:00:00+05:30' } });
    const { qc, h } = setup(messageCard({ id: 1 }));
    qc.setQueryData(queryKeys.history, { cards: [c] });
    await act(async () => { await h.current.lists.putBack(c); });
    expect(ids(qc, queryKeys.history)).toEqual([]);
    const top = qc.getQueryData<{ pages: { cards: Card[] }[] }>(queryKeys.deck)!.pages[0].cards[0];
    expect(top.id).toBe(5);
    expect(top).not.toHaveProperty('swiped');
    expect(enqueued).toEqual([{ key: 'state:5', method: 'POST', path: '/items/5/state', body: { action: 'restore' } }]);
  });
});

// ---------------------------------------------------------------- the sheet under the keyboard
describe('the bottom sheet and the keyboard (the delete-account box)', () => {
  const isView = (n: ReactTestInstance) => typeof n.type === 'string';
  function mountSheet(windowBottom: number) {
    const listeners: Record<string, (e?: unknown) => void> = {};
    jest.spyOn(Keyboard, 'addListener').mockImplementation(((name: string, fn: (e?: unknown) => void) => { listeners[name] = fn; return { remove: jest.fn() }; }) as never);
    // React Native's Jest mock of <View> gives every View a no-op measureInWindow; make it report a window of 400 x windowBottom.
    const { View } = jest.requireActual('react-native');
    jest.spyOn(View.prototype, 'measureInWindow').mockImplementation(((cb: (x: number, y: number, w: number, h: number) => void) => cb(0, 0, 400, windowBottom)) as never);
    let r!: ReturnType<typeof create>;
    act(() => { r = create(<Sheet visible title="Delete your account?" onClose={() => undefined}><></></Sheet>); });
    // a real phone reports the window's size when it lays the sheet out
    const root = r.root.findAll((n) => typeof n.type === 'string' && typeof n.props.onLayout === 'function')[0];
    act(() => root.props.onLayout({ nativeEvent: { layout: { height: windowBottom } } }));
    const spacer = () => r.root.findAll((n) => isView(n) && n.props.testID === 'keyboard-spacer')[0];
    const height = () => (Object.assign({}, ...[spacer().props.style].flat(3).filter(Boolean)) as { height: number }).height;
    return { listeners, height, r };
  }
  beforeEach(() => { jest.useFakeTimers(); });
  afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks(); });

  test('before the keyboard opens nothing is added', () => {
    expect(mountSheet(2400).height()).toBe(0);
  });
  test('when the keyboard covers the sheet\'s window, the sheet is lifted by exactly the part that is covered', () => {
    const { listeners, height } = mountSheet(2400);
    act(() => { listeners.keyboardDidShow({ endCoordinates: { screenY: 1500, height: 900 } }); jest.advanceTimersByTime(200); });
    expect(height()).toBe(900);
  });
  test('when the phone already shrank the window for the keyboard, nothing extra is added (no double adjustment)', () => {
    const { listeners, height } = mountSheet(1500);
    act(() => { listeners.keyboardDidShow({ endCoordinates: { screenY: 1500, height: 900 } }); jest.advanceTimersByTime(200); });
    expect(height()).toBe(0);
  });
  test('closing the keyboard puts the sheet back', () => {
    const { listeners, height } = mountSheet(2400);
    act(() => { listeners.keyboardDidShow({ endCoordinates: { screenY: 1500, height: 900 } }); jest.advanceTimersByTime(200); });
    expect(height()).toBe(900);
    act(() => { listeners.keyboardDidHide(); });
    expect(height()).toBe(0);
  });
  test('it also listens only once and cleans up after itself', () => {
    const remove = jest.fn();
    jest.spyOn(Keyboard, 'addListener').mockImplementation((() => ({ remove })) as never);
    let r!: ReturnType<typeof create>;
    act(() => { r = create(<Sheet visible title="t" onClose={() => undefined}><></></Sheet>); });
    expect(Keyboard.addListener).toHaveBeenCalledTimes(2);
    act(() => r.unmount());
    expect(remove).toHaveBeenCalledTimes(2);
  });
});
