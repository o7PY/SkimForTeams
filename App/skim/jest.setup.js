// Reanimated and worklets under Jest. The library's own mock still loads native CSS code, so this is a small mock of exactly what the
// app uses: shared values are plain boxes, animations land at once (their callbacks fire with finished = true), so a "fly out" is
// synchronous and the swipe tests can drive it.
jest.mock('react-native-worklets', () => ({ scheduleOnRN: (fn, ...args) => fn(...args) }));
jest.mock('react-native-reanimated', () => {
  const React = require('react');
  const RN = require('react-native');
  const land = (to, _config, cb) => {
    if (typeof cb === 'function') cb(true);
    return to;
  };
  const strip = (Comp) => React.forwardRef(({ entering, exiting, ...props }, ref) => React.createElement(Comp, { ref, ...props }));
  const layout = () => {
    const o = { duration: () => o, delay: () => o, withInitialValues: () => o };
    return o;
  };
  return {
    __esModule: true,
    default: { View: strip(RN.View), Text: strip(RN.Text), createAnimatedComponent: (c) => c },
    useSharedValue: (initial) => {
      const ref = React.useRef(null);
      if (!ref.current) ref.current = { value: initial, get() { return this.value; }, set(next) { this.value = next; } };
      return ref.current;
    },
    useAnimatedStyle: (build) => build(),
    useReducedMotion: () => global.__REDUCED_MOTION__ === true,
    withTiming: land,
    withSpring: land,
    withSequence: (...steps) => steps[steps.length - 1],
    withRepeat: (animation) => animation,
    Easing: { out: (f) => f, in: (f) => f, quad: (v) => v, cubic: (v) => v },
    Extrapolation: { CLAMP: 'clamp' },
    interpolate: (v, input, output) => {
      const p = Math.min(1, Math.max(0, (v - input[0]) / (input[1] - input[0])));
      return output[0] + (output[1] - output[0]) * p;
    },
    FadeIn: layout(), FadeOut: layout(), FadeInDown: layout(), SlideInDown: layout(), SlideOutDown: layout(),
  };
});
// No phone to buzz: record the calls instead (the haptics tests read them).
jest.mock('expo-haptics', () => ({
  impactAsync: jest.fn(() => Promise.resolve()),
  selectionAsync: jest.fn(() => Promise.resolve()),
  ImpactFeedbackStyle: { Light: 'light', Medium: 'medium', Heavy: 'heavy' },
}));
// Online by default (the offline banner reads it); tests that care mock expo-network themselves.
jest.mock('expo-network', () => ({
  useNetworkState: () => ({ isConnected: true, isInternetReachable: true }),
  getNetworkStateAsync: () => Promise.resolve({ isConnected: true, isInternetReachable: true }),
  addNetworkStateListener: () => ({ remove: () => {} }),
}));
