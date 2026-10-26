// The swipe decision (P4-D-111): left = read (dismiss), right = open. These run on the UI thread inside the gesture, hence 'worklet'.
export type SwipeOutcome = 'dismiss' | 'open' | 'none';

export const DISTANCE_FRACTION = 0.3; // of the screen width
export const FLICK_VELOCITY = 800; // points per second
export const MIN_FLICK_TRAVEL = 24; // a flick must also have moved this far the same way

export function swipeOutcome(translationX: number, velocityX: number, width: number): SwipeOutcome {
  'worklet';
  const far = width * DISTANCE_FRACTION;
  if (translationX <= -far || (velocityX <= -FLICK_VELOCITY && translationX <= -MIN_FLICK_TRAVEL)) return 'dismiss';
  if (translationX >= far || (velocityX >= FLICK_VELOCITY && translationX >= MIN_FLICK_TRAVEL)) return 'open';
  return 'none';
}

/** 0..1: how far along a decision the drag is, for the READ / OPEN stamps. */
export function stampProgress(translationX: number, width: number, dir: -1 | 1): number {
  'worklet';
  const p = (translationX * dir) / (width * DISTANCE_FRACTION);
  return p <= 0 ? 0 : p >= 1 ? 1 : p;
}

/** Tilt in degrees for a drag of `translationX`. */
export function tilt(translationX: number, width: number): number {
  'worklet';
  const t = (translationX / width) * 14;
  return t > 14 ? 14 : t < -14 ? -14 : t;
}
