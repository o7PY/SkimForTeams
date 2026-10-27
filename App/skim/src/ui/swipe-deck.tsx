// The swipe stack (P4-D-111). The top card follows the finger on the UI thread; nothing in the drag touches React state, so it stays
// at the display's frame rate. Left = dismiss, right = open. Everything the gesture can do is also a button or an accessibility action.
// Motion (P5-D-106/107): a tilt of at most 8 degrees, a critically damped spring back, a 200 ms fly-out, the next card settling from
// 0.96 to 1. With "reduce motion" on, the card does not tilt or fly: it fades out instead.
// PHASE6A: when `onAsk` is given, dragging UP asks about the top card (the card is NOT dismissed). The direction is locked to one
// axis after about 10 px, so an upward drag never fights a sideways one; with no `onAsk` the gesture is exactly what it was.
import { useCallback, useImperativeHandle, useLayoutEffect, useRef, type Ref } from 'react';
import { useWindowDimensions, View } from 'react-native';
import { GestureDetector, usePanGesture } from 'react-native-gesture-handler';
import Animated, { Easing, Extrapolation, interpolate, useAnimatedStyle, useReducedMotion, useSharedValue, withSpring, withTiming } from 'react-native-reanimated';
import { scheduleOnRN } from 'react-native-worklets';

import { makeStyles, radius, space, tint, useTheme } from '@/constants/theme';
import { haptics } from '@/lib/haptics';
import type { Card } from '@/lib/types';
import { askOutcome, askProgress, AXIS_LOCK_PX, lockAxis, stampProgress, swipeOutcome, tilt, type SwipeOutcome } from '@/lib/swipe';
import { AppText } from './app-text';
import { DeckCard } from './deck-card';

export type SwipeDeckHandle = { fling(outcome: 'dismiss' | 'open'): void };
type Props = { cards: Card[]; onDismiss(card: Card): void; onOpen(card: Card): void; onAsk?(card: Card): void; handle?: Ref<SwipeDeckHandle> };
const NEVER = 100_000; // an offset the finger can never reach: "no limit" on that side

const VISIBLE = 3;
const FLY_MS = 200;
const FADE_MS = 150;
// Critically damped (damping ratio ~0.93): settles in about 250 ms with no visible overshoot.
const SPRING = { damping: 30, stiffness: 260 } as const;

export function SwipeDeck({ cards, onDismiss, onOpen, onAsk, handle }: Props) {
  const styles = useStyles();
  const t = useTheme();
  const reduced = useReducedMotion();
  const { width, height } = useWindowDimensions();
  const x = useSharedValue(0);
  const y = useSharedValue(0);
  const axis = useSharedValue(0); // 0 undecided, 1 sideways, 2 upward: locked for the rest of one drag
  const askDrag = useSharedValue(0); // how far up the finger is (<= 0), for the ASK label
  const askOn = !!onAsk;
  const fade = useSharedValue(1);
  const topId = cards[0]?.id ?? null;

  // The latest props, read when a swipe lands (the gesture closure would otherwise hold the cards of an older render).
  const latest = useRef({ cards, onDismiss, onOpen, onAsk });
  useLayoutEffect(() => {
    latest.current = { cards, onDismiss, onOpen, onAsk };
  });

  // The swiped card is gone in the same render that promotes the next one: put the drag back to centre before it paints.
  useLayoutEffect(() => {
    x.set(0);
    y.set(0);
    fade.set(1);
  }, [topId, x, y, fade]);

  const commit = useCallback((outcome: SwipeOutcome) => {
    const { cards: list, onDismiss: dismiss, onOpen: open } = latest.current;
    const card = list[0];
    if (!card || outcome === 'none') return;
    haptics.impact();
    if (outcome === 'dismiss') dismiss(card);
    else open(card);
  }, []);

  const fly = useCallback(
    (outcome: 'dismiss' | 'open') => {
      'worklet';
      const landed = (finished?: boolean) => {
        'worklet';
        if (finished) scheduleOnRN(commit, outcome);
      };
      if (reduced) fade.set(withTiming(0, { duration: FADE_MS }, landed));
      else x.set(withTiming((outcome === 'dismiss' ? -1 : 1) * width * 1.3, { duration: FLY_MS, easing: Easing.out(Easing.quad) }, landed));
    },
    [x, fade, width, commit, reduced],
  );

  const ask = useCallback(() => {
    const card = latest.current.cards[0];
    if (card) latest.current.onAsk?.(card);
  }, []);

  useImperativeHandle(handle, () => ({ fling: (o) => cards.length > 0 && fly(o) }), [cards.length, fly]);

  const pan = usePanGesture({
    enabled: cards.length > 0,
    // Asking on: either axis can start the drag (up, or sideways), downward still fails so pull-to-refresh keeps working.
    activeOffsetX: askOn ? [-AXIS_LOCK_PX, AXIS_LOCK_PX] : [-12, 12],
    activeOffsetY: askOn ? ([-AXIS_LOCK_PX, NEVER] as [number, number]) : undefined,
    failOffsetY: askOn ? ([-NEVER, 20] as [number, number]) : ([-20, 20] as [number, number]),
    onBegin: () => {
      axis.set(0);
      askDrag.set(0);
    },
    onUpdate: (e) => {
      if (askOn && axis.get() === 0) {
        const locked = lockAxis(e.translationX, e.translationY);
        if (locked === null) return;
        axis.set(locked === 'x' ? 1 : 2);
      }
      if (askOn && axis.get() === 2) {
        askDrag.set(Math.min(0, e.translationY));
        y.set(reduced ? 0 : Math.min(0, e.translationY) * 0.35); // the card follows an upward drag, only upward, and a little less
        return;
      }
      x.set(e.translationX);
      y.set(reduced ? 0 : e.translationY * 0.15);
    },
    onDeactivate: (e) => {
      if (askOn && axis.get() === 2) {
        axis.set(0);
        askDrag.set(withTiming(0, { duration: FADE_MS }));
        y.set(reduced ? withTiming(0, { duration: FADE_MS }) : withSpring(0, SPRING));
        if (askOutcome(e.translationY, e.velocityY, height)) scheduleOnRN(ask);
        return;
      }
      axis.set(0);
      const outcome = swipeOutcome(e.translationX, e.velocityX, width);
      if (outcome === 'none') {
        x.set(reduced ? withTiming(0, { duration: FADE_MS }) : withSpring(0, SPRING));
        y.set(reduced ? withTiming(0, { duration: FADE_MS }) : withSpring(0, SPRING));
        return;
      }
      fly(outcome);
    },
  });

  const topStyle = useAnimatedStyle(() => ({
    opacity: fade.get(),
    transform: [{ translateX: x.get() }, { translateY: y.get() }, { rotate: reduced ? '0deg' : `${tilt(x.get(), width)}deg` }],
  }));
  const dismissLabel = useAnimatedStyle(() => ({ opacity: stampProgress(x.get(), width, -1) }));
  const openLabel = useAnimatedStyle(() => ({ opacity: stampProgress(x.get(), width, 1) }));
  const askLabel = useAnimatedStyle(() => ({ opacity: askProgress(askDrag.get(), height) }));

  const visible = cards.slice(0, VISIBLE);
  return (
    <View style={styles.stack}>
      {visible
        .map((card, depth) => ({ card, depth }))
        .reverse() // paint the back cards first
        .map(({ card, depth }) =>
          depth === 0 ? (
            <GestureDetector key={card.id} gesture={pan}>
              <Animated.View
                renderToHardwareTextureAndroid
                accessible
                accessibilityActions={[{ name: 'dismiss', label: 'Dismiss' }, { name: 'open', label: 'Open' }, ...(askOn ? [{ name: 'ask', label: 'Ask about this card' }] : [])]}
                onAccessibilityAction={(e) => (e.nativeEvent.actionName === 'ask' ? ask() : fly(e.nativeEvent.actionName === 'open' ? 'open' : 'dismiss'))}
                style={[styles.card, topStyle]}>
                <DeckCard card={card} />
                <Animated.View pointerEvents="none" style={[styles.label, styles.labelRight, dismissLabel]}>
                  <AppText variant="label" color={t.textPrimary}>DISMISS</AppText>
                </Animated.View>
                <Animated.View pointerEvents="none" style={[styles.label, styles.labelLeft, openLabel]}>
                  <AppText variant="label" color={t.primary}>OPEN</AppText>
                </Animated.View>
                {askOn ? (
                  <Animated.View pointerEvents="none" style={[styles.label, styles.labelBottom, askLabel]}>
                    <AppText variant="label" color={t.update}>ASK</AppText>
                  </Animated.View>
                ) : null}
              </Animated.View>
            </GestureDetector>
          ) : (
            <BackCard key={card.id} depth={depth} x={x} width={width} />
          ),
        )}
    </View>
  );
}

function BackCard({ depth, x, width }: { depth: number; x: ReturnType<typeof useSharedValue<number>>; width: number }) {
  const styles = useStyles();
  // A plain SOLID shape, no content and no fading: whatever sits behind the top card must never show through it. Each one steps
  // forward one place as the top card is dragged away; the next card goes 0.96 -> 1.
  const style = useAnimatedStyle(() => {
    const p = interpolate(Math.abs(x.get()), [0, width * 0.3], [0, 1], Extrapolation.CLAMP);
    return {
      transform: [{ scale: interpolate(p, [0, 1], [1 - depth * 0.04, 1 - (depth - 1) * 0.04]) }, { translateY: interpolate(p, [0, 1], [depth * 12, (depth - 1) * 12]) }],
    };
  });
  return <Animated.View pointerEvents="none" style={[styles.card, styles.back, style]} />;
}

const useStyles = makeStyles((t) => ({
  stack: { flex: 1 },
  card: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 28 },
  back: { backgroundColor: t.surfaceRaised, borderColor: t.border, borderWidth: 1, borderRadius: radius.lg },
  label: { position: 'absolute', top: space.lg, paddingHorizontal: space.md, paddingVertical: 6, borderRadius: radius.md, borderWidth: 1.5 },
  labelRight: { right: space.lg, borderColor: t.border, backgroundColor: t.inset },
  labelLeft: { left: space.lg, borderColor: t.primaryBorder, backgroundColor: t.primarySoft },
  labelBottom: { top: undefined, bottom: space.lg, alignSelf: 'center', borderColor: t.update, backgroundColor: tint(t.update, t.surfaceRaised, 0.2) },
}));
