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
  // Hands the config back, and remembers it so a test can drive the real onBegin / onUpdate / onDeactivate callbacks.
  usePanGesture: (config: unknown) => {
    (global as { __pan?: unknown }).__pan = config;
    return config;
  },
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


// ---------------------------------------------------------------- swipe UP = ask (PHASE6A)
type Drag = { translationX: number; translationY: number; velocityX?: number; velocityY?: number };
type Pan = { activeOffsetX: number[]; activeOffsetY?: number[]; failOffsetY: number[]; onBegin(): void; onUpdate(e: Drag): void; onDeactivate(e: Drag): void };
const pan = () => (global as { __pan?: unknown }).__pan as Pan;
const drag = (steps: Drag[], end: Drag) => act(() => {
  pan().onBegin();
  steps.forEach((e) => pan().onUpdate(e));
  pan().onDeactivate(end);
});
const mountAsk = (withAsk = true) => {
  const onDismiss = jest.fn(), onOpen = jest.fn(), onAsk = jest.fn();
  let r!: ReactTestRenderer;
  act(() => {
    r = create(<SwipeDeck cards={cards([7, 8])} onDismiss={onDismiss} onOpen={onOpen} onAsk={withAsk ? onAsk : undefined} />);
  });
  return { r, onDismiss, onOpen, onAsk };
};
// (the Jest window is 1334 high, so 25% is about 333 px)

describe('swipe up asks about the top card', () => {
  test('dragging up about a quarter of the screen asks, and the card is not dismissed or opened', () => {
    const { onAsk, onDismiss, onOpen } = mountAsk();
    drag([{ translationX: 2, translationY: -30 }, { translationX: 4, translationY: -200 }, { translationX: 5, translationY: -340 }], { translationX: 5, translationY: -340, velocityX: 0, velocityY: -50 });
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(onAsk.mock.calls[0][0].id).toBe(7);                       // the TOP card
    expect(onDismiss).not.toHaveBeenCalled();
    expect(onOpen).not.toHaveBeenCalled();
    expect(Haptics.impactAsync).not.toHaveBeenCalled();             // no dismiss/open haptic: nothing was swiped away
  });
  test('a quick upward flick asks after a short pull', () => {
    const { onAsk } = mountAsk();
    drag([{ translationX: 1, translationY: -30 }, { translationX: 1, translationY: -60 }], { translationX: 1, translationY: -60, velocityY: -1500 });
    expect(onAsk).toHaveBeenCalledTimes(1);
  });
  test('a short, slow upward drag springs back and asks nothing', () => {
    const { onAsk, onDismiss } = mountAsk();
    drag([{ translationX: 0, translationY: -60 }, { translationX: 0, translationY: -120 }], { translationX: 0, translationY: -120, velocityY: -100 });
    expect(onAsk).not.toHaveBeenCalled();
    expect(onDismiss).not.toHaveBeenCalled();
  });
  test('a sideways drag that wobbles upward is still a sideways swipe: it dismisses and never asks', () => {
    const { onAsk, onDismiss } = mountAsk();
    // locked to x at the first 10+ px; the later big upward wobble must not turn it into an ask
    drag([{ translationX: -30, translationY: -5 }, { translationX: -250, translationY: -400 }, { translationX: -400, translationY: -700 }],
      { translationX: -400, translationY: -700, velocityX: -300, velocityY: -900 });
    expect(onAsk).not.toHaveBeenCalled();
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onDismiss.mock.calls[0][0].id).toBe(7);
  });
  test('an upward drag that wanders sideways is still an ask and never opens or dismisses', () => {
    const { onAsk, onDismiss, onOpen } = mountAsk();
    drag([{ translationX: 2, translationY: -40 }, { translationX: 300, translationY: -250 }, { translationX: 500, translationY: -400 }],
      { translationX: 500, translationY: -400, velocityX: 400, velocityY: -200 });
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
    expect(onDismiss).not.toHaveBeenCalled();
  });
  test('a downward drag asks nothing (pull-to-refresh and the old rules are untouched)', () => {
    const { onAsk, onDismiss, onOpen } = mountAsk();
    drag([{ translationX: 0, translationY: 120 }, { translationX: 0, translationY: 500 }], { translationX: 0, translationY: 500, velocityY: 2000 });
    expect(onAsk).not.toHaveBeenCalled();
    expect(onDismiss).not.toHaveBeenCalled();
    expect(onOpen).not.toHaveBeenCalled();
  });
  test('plain left and right swipes still dismiss and open exactly as before, with the ask feature on', () => {
    const a = mountAsk();
    drag([{ translationX: -40, translationY: 3 }, { translationX: -330, translationY: 8 }], { translationX: -330, translationY: 8, velocityX: -200, velocityY: 0 });
    expect(a.onDismiss).toHaveBeenCalledTimes(1);
    const b = mountAsk();
    drag([{ translationX: 40, translationY: -3 }, { translationX: 330, translationY: -8 }], { translationX: 330, translationY: -8, velocityX: 200, velocityY: 0 });
    expect(b.onOpen).toHaveBeenCalledTimes(1);
    expect(a.onAsk).not.toHaveBeenCalled();
    expect(b.onAsk).not.toHaveBeenCalled();
  });
  test('the next drag starts clean: nothing is left over from the last one', () => {
    const { onAsk, onDismiss } = mountAsk();
    drag([{ translationX: 0, translationY: -340 }], { translationX: 0, translationY: -340, velocityY: 0 });   // an ask
    drag([{ translationX: -30, translationY: -2 }, { translationX: -330, translationY: -4 }], { translationX: -330, translationY: -4, velocityX: 0, velocityY: 0 });
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
  test('with chat off (no onAsk) the gesture is exactly the old one: sideways only, upward does nothing', () => {
    const { onAsk, onDismiss } = mountAsk(false);
    expect(pan().activeOffsetX).toEqual([-12, 12]);
    expect(pan().failOffsetY).toEqual([-20, 20]);
    expect(pan().activeOffsetY).toBeUndefined();
    drag([{ translationX: 0, translationY: -340 }], { translationX: 0, translationY: -340, velocityY: -2000 });
    expect(onAsk).not.toHaveBeenCalled();
    expect(onDismiss).not.toHaveBeenCalled();
  });
  test('with chat on, the gesture locks after about 10 px, activates on up or sideways, and still fails on a downward drag', () => {
    mountAsk();
    expect(pan().activeOffsetX).toEqual([-10, 10]);
    expect(pan().activeOffsetY![0]).toBe(-10);
    expect(pan().failOffsetY[1]).toBe(20);
    expect(pan().failOffsetY[0]).toBeLessThan(-1000);
  });
  test('the card offers "Ask about this card" to a screen reader, only when chat is on, and it asks the top card', () => {
    const on = mountAsk();
    const actions = on.r.root.findAll((n) => Array.isArray(n.props.accessibilityActions))[0].props.accessibilityActions as { name: string }[];
    expect(actions.map((a) => a.name)).toEqual(['dismiss', 'open', 'ask']);
    act(() => on.r.root.findAll((n) => typeof n.props.onAccessibilityAction === 'function')[0].props.onAccessibilityAction({ nativeEvent: { actionName: 'ask' } }));
    expect(on.onAsk).toHaveBeenCalledTimes(1);
    expect(on.onAsk.mock.calls[0][0].id).toBe(7);
    expect(on.onDismiss).not.toHaveBeenCalled();
    const off = mountAsk(false);
    const offActions = off.r.root.findAll((n) => Array.isArray(n.props.accessibilityActions))[0].props.accessibilityActions as { name: string }[];
    expect(offActions.map((a) => a.name)).toEqual(['dismiss', 'open']);
  });
});
