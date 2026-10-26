// The swipe decision (P4-D-111): left = read (dismiss), right = open. These run on the UI thread inside the gesture, hence 'worklet'.
export type SwipeOutcome = 'dismiss' | 'open' | 'none';

export const DISTANCE_FRACTION = 0.35; // of the screen width: a slow drag must go about this far (a quick flick needs far less)
export const FLICK_VELOCITY = 800; // points per second
export const MIN_FLICK_TRAVEL = 24; // a flick must also have moved this far the same way

export function swipeOutcome(translationX: number, velocityX: number, width: number): SwipeOutcome {
  'worklet';
  const far = width * DISTANCE_FRACTION;
  if (translationX <= -far || (velocityX <= -FLICK_VELOCITY && translationX <= -MIN_FLICK_TRAVEL)) return 'dismiss';
  if (translationX >= far || (velocityX >= FLICK_VELOCITY && translationX >= MIN_FLICK_TRAVEL)) return 'open';
  return 'none';
}

/** 0..1: how far along a decision the drag is, for the DISMISS / OPEN labels. */
export function stampProgress(translationX: number, width: number, dir: -1 | 1): number {
  'worklet';
  const p = (translationX * dir) / (width * DISTANCE_FRACTION);
  return p <= 0 ? 0 : p >= 1 ? 1 : p;
}

export const MAX_TILT = 8; // degrees (P5-D-106/107: a slight tilt)

/** Tilt in degrees for a drag of `translationX`. */
export function tilt(translationX: number, width: number): number {
  'worklet';
  const t = (translationX / width) * 10;
  return t > MAX_TILT ? MAX_TILT : t < -MAX_TILT ? -MAX_TILT : t;
}

/**
 * Applies each landed swipe to its card once. A fast double tap, or a fling that lands twice before the deck has re-rendered, would
 * otherwise dismiss the same card twice; the gate lets a card through once, and lets it through again after `holdMs` (so a card that
 * stayed on top, e.g. after a failed action, is never stuck).
 */
export function createSwipeGate(holdMs = 1000) {
  let lastId: number | null = null;
  let lastAt = 0;
  return {
    take(cardId: number, now = Date.now()): boolean {
      if (lastId === cardId && now - lastAt < holdMs) return false;
      lastId = cardId;
      lastAt = now;
      return true;
    },
    reset() {
      lastId = null;
    },
  };
}
