// The small motions of P5-D-107, in one place. All run on the UI thread (Reanimated), last 150-300 ms, never loop, and swap movement
// for a short fade when the phone's "reduce motion" setting is on (P5-D-106).
import { useEffect, useRef, useState, type ReactNode } from 'react';
import Animated, { FadeIn, FadeInDown, FadeOut, SlideInDown, SlideOutDown, useAnimatedStyle, useReducedMotion, useSharedValue, withSequence, withTiming } from 'react-native-reanimated';

/** Fires once each time `trigger` turns true (never on first mount): `pop` is 1 -> 1.15 -> 1 (the star), `tick` is 0.8 -> 1 (a checkbox). */
export function Pop({ trigger, kind, children }: { trigger: boolean; kind: 'pop' | 'tick'; children: ReactNode }) {
  const reduced = useReducedMotion();
  const scale = useSharedValue(1);
  const opacity = useSharedValue(1);
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    if (!trigger) return;
    if (reduced) {
      opacity.set(0.4);
      opacity.set(withTiming(1, { duration: 150 }));
    } else if (kind === 'pop') {
      scale.set(withSequence(withTiming(1.15, { duration: 90 }), withTiming(1, { duration: 120 })));
    } else {
      scale.set(0.8);
      scale.set(withTiming(1, { duration: 150 }));
    }
  }, [trigger, kind, reduced, scale, opacity]);
  const style = useAnimatedStyle(() => ({ opacity: opacity.get(), transform: [{ scale: scale.get() }] }));
  return <Animated.View style={style}>{children}</Animated.View>;
}

const revealed = new Set<string>();

/** A list row that fades in once, the first time its list is shown this session (rows scrolled into view later never animate). */
export function Reveal({ scope, index, children }: { scope: string; index: number; children: ReactNode }) {
  const [first] = useState(() => !revealed.has(scope)); // decided at mount, so every row of the first batch shares it
  useEffect(() => {
    revealed.add(scope);
  }, [scope]);
  return <Animated.View entering={first && index < 10 ? FadeIn.duration(200).delay(index * 30) : undefined}>{children}</Animated.View>;
}

/** The detail screen opens with a fade and a 24 px rise (a plain fade with reduced motion). */
export function RiseIn({ children }: { children: ReactNode }) {
  const reduced = useReducedMotion();
  const entering = reduced ? FadeIn.duration(150) : FadeInDown.duration(220).withInitialValues({ opacity: 0, transform: [{ translateY: 24 }] });
  return <Animated.View entering={entering} style={{ flex: 1 }}>{children}</Animated.View>;
}

/** The undo toast slides up from the bottom and back down (fades with reduced motion). */
export function useToastMotion() {
  const reduced = useReducedMotion();
  return reduced
    ? { entering: FadeIn.duration(150), exiting: FadeOut.duration(150) }
    : { entering: SlideInDown.duration(220), exiting: SlideOutDown.duration(180) };
}
