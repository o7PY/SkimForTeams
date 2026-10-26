// The swipe stack's guarantees (P5-D-107/108, acceptance B4/B5): a swipe applies exactly once to the card that flew, rapid repeats
// never double-apply or drop a card, a light haptic fires only when a swipe commits, and reduced motion still completes the swipe.
import * as Haptics from 'expo-haptics';
import { createRef } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { SwipeDeck, type SwipeDeckHandle } from '@/ui/swipe-deck';
import { messageCard } from './fixtures';

// The icon font is a binary asset Jest cannot resolve: a stub that prints the icon's name.
jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});

// The gesture itself needs a device; its outcome logic is tested in swipe.test.ts. Here the deck is driven through its handle.
jest.mock('react-native-gesture-handler', () => ({
  GestureDetector: ({ children }: { children: unknown }) => children,
  usePanGesture: (config: unknown) => config,
}));

const cards = (ids: number[]) => ids.map((id) => messageCard({ id }));
const mount = (ids: number[], onDismiss = jest.fn(), onOpen = jest.fn()) => {
  const handle = createRef<SwipeDeckHandle>();
  let r!: ReactTestRenderer;
  act(() => {
    r = create(<SwipeDeck cards={cards(ids)} onDismiss={onDismiss} onOpen={onOpen} handle={handle} />);
  });
  const set = (next: number[]) => act(() => r.update(<SwipeDeck cards={cards(next)} onDismiss={onDismiss} onOpen={onOpen} handle={handle} />));
  return { handle, onDismiss, onOpen, set };
};

beforeEach(() => {
  jest.clearAllMocks();
  (global as { __REDUCED_MOTION__?: boolean }).__REDUCED_MOTION__ = false;
});

test('a dismiss applies to the top card, once, with one light haptic', () => {
  const { handle, onDismiss, onOpen } = mount([1, 2, 3]);
  act(() => handle.current!.fling('dismiss'));
  expect(onDismiss).toHaveBeenCalledTimes(1);
  expect(onDismiss.mock.calls[0][0].id).toBe(1);
  expect(onOpen).not.toHaveBeenCalled();
  expect(Haptics.impactAsync).toHaveBeenCalledTimes(1);
  expect(Haptics.impactAsync).toHaveBeenCalledWith('light');
  expect(Haptics.selectionAsync).not.toHaveBeenCalled();
});

test('rapid repeats before the deck re-renders never apply the same card twice', () => {
  const { handle, onDismiss } = mount([1, 2, 3]);
  act(() => {
    handle.current!.fling('dismiss');
    handle.current!.fling('dismiss');
    handle.current!.fling('open');
    handle.current!.fling('dismiss');
  });
  expect(onDismiss).toHaveBeenCalledTimes(1);
  expect(onDismiss.mock.calls[0][0].id).toBe(1);
  expect(Haptics.impactAsync).toHaveBeenCalledTimes(1);
});

test('a fast run of swipes takes every card exactly once, in order, losing none', () => {
  const { handle, onDismiss, onOpen, set } = mount([1, 2, 3, 4, 5, 6]);
  let left = [1, 2, 3, 4, 5, 6];
  const seen: number[] = [];
  for (let i = 0; i < 6; i++) {
    act(() => {
      handle.current!.fling(i % 2 ? 'open' : 'dismiss');
      handle.current!.fling('dismiss'); // an impatient second tap on the same card
    });
    seen.push(...[...onDismiss.mock.calls, ...onOpen.mock.calls].map((c) => c[0].id).filter((id) => !seen.includes(id)));
    left = left.slice(1);
    set(left);
  }
  expect(seen).toEqual([1, 2, 3, 4, 5, 6]);
  expect(onDismiss.mock.calls.length + onOpen.mock.calls.length).toBe(6);
  expect(Haptics.impactAsync).toHaveBeenCalledTimes(6);
});

test('a card put back by Undo (top again) can be swiped again', () => {
  const { handle, onDismiss, set } = mount([1, 2]);
  act(() => handle.current!.fling('dismiss'));
  set([2]); // card 1 gone
  set([1, 2]); // Undo
  act(() => handle.current!.fling('dismiss'));
  expect(onDismiss.mock.calls.map((c) => c[0].id)).toEqual([1, 1]);
});

test('with reduced motion the swipe still applies (a fade instead of a fly-out)', () => {
  (global as { __REDUCED_MOTION__?: boolean }).__REDUCED_MOTION__ = true;
  const { handle, onOpen } = mount([7, 8]);
  act(() => handle.current!.fling('open'));
  expect(onOpen).toHaveBeenCalledTimes(1);
  expect(onOpen.mock.calls[0][0].id).toBe(7);
});

test('an empty deck ignores a fling', () => {
  const { handle, onDismiss } = mount([]);
  act(() => handle.current!.fling('dismiss'));
  expect(onDismiss).not.toHaveBeenCalled();
});
