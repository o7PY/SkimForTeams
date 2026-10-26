// The swipe stack (P4-D-111). The top card follows the finger on the UI thread; nothing in the drag touches React state, so it stays
// at the display's frame rate. Left = dismiss, right = open. Everything the gesture can do is also a button or an accessibility action.
// Motion (P5-D-106/107): a tilt of at most 8 degrees, a critically damped spring back, a 200 ms fly-out, the next card settling from
// 0.96 to 1. With "reduce motion" on, the card does not tilt or fly: it fades out instead.
import { useCallback, useImperativeHandle, useLayoutEffect, useRef, type Ref } from 'react';
import { useWindowDimensions, View } from 'react-native';
import { GestureDetector, usePanGesture } from 'react-native-gesture-handler';
import Animated, { Easing, Extrapolation, interpolate, useAnimatedStyle, useReducedMotion, useSharedValue, withSpring, withTiming } from 'react-native-reanimated';
import { scheduleOnRN } from 'react-native-worklets';

import { makeStyles, radius, space, useTheme } from '@/constants/theme';
import { haptics } from '@/lib/haptics';
import type { Card } from '@/lib/types';
import { createSwipeGate, stampProgress, swipeOutcome, tilt, type SwipeOutcome } from '@/lib/swipe';
import { AppText } from './app-text';
import { DeckCard } from './deck-card';

export type SwipeDeckHandle = { fling(outcome: 'dismiss' | 'open'): void };
type Props = { cards: Card[]; onDismiss(card: Card): void; onOpen(card: Card): void; handle?: Ref<SwipeDeckHandle> };

const VISIBLE = 3;
const FLY_MS = 200;
const FADE_MS = 150;
// Critically damped (damping ratio ~0.93): settles in about 250 ms with no visible overshoot.
const SPRING = { damping: 30, stiffness: 260 } as const;

export function SwipeDeck({ cards, onDismiss, onOpen, handle }: Props) {
  const styles = useStyles();
  const t = useTheme();
  const reduced = useReducedMotion();
  const { width } = useWindowDimensions();
  const x = useSharedValue(0);
  const y = useSharedValue(0);
  const fade = useSharedValue(1);
  const flying = useSharedValue(false); // a card that is already on its way out cannot be flung again
  const topId = cards[0]?.id ?? null;
  const gate = useRef(createSwipeGate());

  // The latest props, read when a swipe lands (the gesture closure would otherwise hold the cards of an older render).
  const latest = useRef({ cards, onDismiss, onOpen });
  useLayoutEffect(() => {
    latest.current = { cards, onDismiss, onOpen };
  });

  // The swiped card is gone in the same render that promotes the next one: put the drag back to centre before it paints.
  useLayoutEffect(() => {
    x.set(0);
    y.set(0);
    fade.set(1);
    flying.set(false);
    gate.current.reset();
  }, [topId, x, y, fade, flying]);

  // Applies a landed swipe to the card that flew: once, and only if it is still the top card.
  const commit = useCallback((outcome: SwipeOutcome, cardId: number) => {
    const { cards: list, onDismiss: dismiss, onOpen: open } = latest.current;
    const card = list[0];
    if (!card || outcome === 'none' || card.id !== cardId || !gate.current.take(card.id)) return;
    haptics.impact();
    if (outcome === 'dismiss') dismiss(card);
    else open(card);
  }, []);

  const fly = useCallback(
    (outcome: 'dismiss' | 'open', cardId: number) => {
      'worklet';
      if (flying.get()) return;
      flying.set(true);
      const landed = (finished?: boolean) => {
        'worklet';
        if (finished) scheduleOnRN(commit, outcome, cardId);
      };
      if (reduced) fade.set(withTiming(0, { duration: FADE_MS }, landed));
      else x.set(withTiming((outcome === 'dismiss' ? -1 : 1) * width * 1.3, { duration: FLY_MS, easing: Easing.out(Easing.quad) }, landed));
    },
    [x, fade, flying, width, commit, reduced],
  );

  useImperativeHandle(handle, () => ({ fling: (o) => { if (latest.current.cards[0]) fly(o, latest.current.cards[0].id); } }), [fly]);

  const pan = usePanGesture({
    enabled: cards.length > 0,
    activeOffsetX: [-12, 12],
    failOffsetY: [-20, 20],
    onUpdate: (e) => {
      if (flying.get()) return;
      x.set(e.translationX);
      y.set(reduced ? 0 : e.translationY * 0.15);
    },
    onDeactivate: (e) => {
      if (flying.get() || topId === null) return;
      const outcome = swipeOutcome(e.translationX, e.velocityX, width);
      if (outcome === 'none') {
        x.set(reduced ? withTiming(0, { duration: FADE_MS }) : withSpring(0, SPRING));
        y.set(reduced ? withTiming(0, { duration: FADE_MS }) : withSpring(0, SPRING));
        return;
      }
      fly(outcome, topId);
    },
  });

  const topStyle = useAnimatedStyle(() => ({
    opacity: fade.get(),
    transform: [{ translateX: x.get() }, { translateY: y.get() }, { rotate: reduced ? '0deg' : `${tilt(x.get(), width)}deg` }],
  }));
  const dismissLabel = useAnimatedStyle(() => ({ opacity: stampProgress(x.get(), width, -1) }));
  const openLabel = useAnimatedStyle(() => ({ opacity: stampProgress(x.get(), width, 1) }));

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
                accessibilityActions={[{ name: 'dismiss', label: 'Dismiss' }, { name: 'open', label: 'Open' }]}
                onAccessibilityAction={(e) => fly(e.nativeEvent.actionName === 'open' ? 'open' : 'dismiss', card.id)}
                style={[styles.card, topStyle]}>
                <DeckCard card={card} />
                <Animated.View pointerEvents="none" style={[styles.label, styles.labelRight, dismissLabel]}>
                  <AppText variant="label" color={t.textPrimary}>DISMISS</AppText>
                </Animated.View>
                <Animated.View pointerEvents="none" style={[styles.label, styles.labelLeft, openLabel]}>
                  <AppText variant="label" color={t.primary}>OPEN</AppText>
                </Animated.View>
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
}));
