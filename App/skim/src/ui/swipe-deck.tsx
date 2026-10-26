// The swipe stack (P4-D-111). The top card follows the finger on the UI thread; nothing in the drag touches React state, so it stays
// at the display's frame rate. Left = read, right = open. Everything the gesture can do is also a button or an accessibility action.
import { useCallback, useImperativeHandle, useLayoutEffect, useRef, type Ref } from 'react';
import { useWindowDimensions, Vibration, View } from 'react-native';
import { GestureDetector, usePanGesture } from 'react-native-gesture-handler';
import Animated, { Extrapolation, interpolate, useAnimatedStyle, useSharedValue, withSpring, withTiming } from 'react-native-reanimated';
import { scheduleOnRN } from 'react-native-worklets';

import { makeStyles, radius, space, useTheme } from '@/constants/theme';
import type { Card } from '@/lib/types';
import { stampProgress, swipeOutcome, tilt, type SwipeOutcome } from '@/lib/swipe';
import { AppText } from './app-text';
import { DeckCard } from './deck-card';

export type SwipeDeckHandle = { fling(outcome: 'dismiss' | 'open'): void };
type Props = { cards: Card[]; onDismiss(card: Card): void; onOpen(card: Card): void; handle?: Ref<SwipeDeckHandle> };

const VISIBLE = 3;
const FLY_MS = 200;

export function SwipeDeck({ cards, onDismiss, onOpen, handle }: Props) {
  const styles = useStyles();
  const t = useTheme();
  const { width } = useWindowDimensions();
  const x = useSharedValue(0);
  const y = useSharedValue(0);
  const topId = cards[0]?.id ?? null;

  // The latest props, read when a swipe lands (the gesture closure would otherwise hold the cards of an older render).
  const latest = useRef({ cards, onDismiss, onOpen });
  useLayoutEffect(() => {
    latest.current = { cards, onDismiss, onOpen };
  });

  // The swiped card is gone in the same render that promotes the next one: put the shared drag back to centre before it paints.
  useLayoutEffect(() => {
    x.set(0);
    y.set(0);
  }, [topId, x, y]);

  const commit = useCallback((outcome: SwipeOutcome) => {
    const { cards: list, onDismiss: dismiss, onOpen: open } = latest.current;
    const card = list[0];
    if (!card || outcome === 'none') return;
    Vibration.vibrate(10); // a tick when a swipe lands (core React Native; expo-haptics would need a new build)
    if (outcome === 'dismiss') dismiss(card);
    else open(card);
  }, []);

  const flyOut = useCallback(
    (outcome: 'dismiss' | 'open') => {
      const dir = outcome === 'dismiss' ? -1 : 1;
      x.set(withTiming(dir * width * 1.3, { duration: FLY_MS }, (finished) => {
        if (finished) scheduleOnRN(commit, outcome);
      }));
    },
    [x, width, commit],
  );

  useImperativeHandle(handle, () => ({ fling: (o) => cards.length > 0 && flyOut(o) }), [cards.length, flyOut]);

  const pan = usePanGesture({
    enabled: cards.length > 0,
    activeOffsetX: [-12, 12],
    failOffsetY: [-20, 20],
    onUpdate: (e) => {
      x.set(e.translationX);
      y.set(e.translationY * 0.15);
    },
    onDeactivate: (e) => {
      const outcome = swipeOutcome(e.translationX, e.velocityX, width);
      if (outcome === 'none') {
        x.set(withSpring(0, { damping: 18, stiffness: 240 }));
        y.set(withSpring(0, { damping: 18, stiffness: 240 }));
        return;
      }
      const dir = outcome === 'dismiss' ? -1 : 1;
      x.set(withTiming(dir * width * 1.3, { duration: FLY_MS }, (finished) => {
        if (finished) scheduleOnRN(commit, outcome);
      }));
    },
  });

  const topStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: x.get() }, { translateY: y.get() }, { rotate: `${tilt(x.get(), width)}deg` }],
  }));
  const readStamp = useAnimatedStyle(() => ({ opacity: stampProgress(x.get(), width, -1) }));
  const openStamp = useAnimatedStyle(() => ({ opacity: stampProgress(x.get(), width, 1) }));

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
                accessibilityActions={[{ name: 'dismiss', label: 'Mark as read' }, { name: 'open', label: 'Open' }]}
                onAccessibilityAction={(e) => flyOut(e.nativeEvent.actionName === 'open' ? 'open' : 'dismiss')}
                style={[styles.card, topStyle]}>
                <DeckCard card={card} />
                <Animated.View pointerEvents="none" style={[styles.stamp, styles.stampRight, readStamp]}>
                  <AppText variant="label" color={t.primary}>READ</AppText>
                </Animated.View>
                <Animated.View pointerEvents="none" style={[styles.stamp, styles.stampLeft, openStamp]}>
                  <AppText variant="label" color={t.textPrimary}>OPTIONS</AppText>
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
  // forward one place as the top card is dragged away.
  const style = useAnimatedStyle(() => {
    const p = interpolate(Math.abs(x.get()), [0, width * 0.3], [0, 1], Extrapolation.CLAMP);
    return {
      transform: [{ scale: interpolate(p, [0, 1], [1 - depth * 0.05, 1 - (depth - 1) * 0.05]) }, { translateY: interpolate(p, [0, 1], [depth * 14, (depth - 1) * 14]) }],
    };
  });
  return <Animated.View pointerEvents="none" style={[styles.card, styles.back, style]} />;
}

const useStyles = makeStyles((t) => ({
  stack: { flex: 1 },
  card: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 28 },
  back: { backgroundColor: t.surfaceRaised, borderColor: t.border, borderWidth: 1, borderRadius: radius.lg },
  stamp: { position: 'absolute', top: space.lg, paddingHorizontal: space.md, paddingVertical: 6, borderRadius: radius.md, borderWidth: 1.5 },
  stampRight: { right: space.lg, borderColor: t.primaryBorder, backgroundColor: t.primarySoft },
  stampLeft: { left: space.lg, borderColor: t.border, backgroundColor: t.inset },
}));
