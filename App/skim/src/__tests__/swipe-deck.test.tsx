// The swipe stack still does what Phase 4 did (a swipe applies to the top card), now with a light haptic when it lands, and reduced
// motion still completes the swipe (P5-D-107/108).
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

test('swiping card after card works in order, and a card put back by Undo can be swiped again', () => {
  const { handle, onDismiss, set } = mount([1, 2, 3]);
  act(() => handle.current!.fling('dismiss'));
  set([2, 3]);
  act(() => handle.current!.fling('dismiss'));
  set([1, 3]); // Undo put card 1 back on top
  act(() => handle.current!.fling('dismiss'));
  expect(onDismiss.mock.calls.map((c) => c[0].id)).toEqual([1, 2, 1]);
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
