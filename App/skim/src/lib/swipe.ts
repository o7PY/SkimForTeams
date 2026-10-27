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

// ---------------------------------------------------------------- swipe UP on the top card = ask about it (PHASE6A)
export const AXIS_LOCK_PX = 10;     // after this much movement the gesture belongs to ONE axis for the rest of the drag
export const ASK_FRACTION = 0.25;   // of the screen height, dragged up
export type Axis = 'x' | 'y';

/** Which way is this drag going? null until it has moved AXIS_LOCK_PX; then the dominant axis (a tie goes to x). The caller keeps the
 *  answer for the whole drag, so an upward swipe never fights a sideways one. */
export function lockAxis(dx: number, dy: number): Axis | null {
  'worklet';
  const ax = dx < 0 ? -dx : dx;
  const ay = dy < 0 ? -dy : dy;
  if ((ax > ay ? ax : ay) < AXIS_LOCK_PX) return null;
  return ax >= ay ? 'x' : 'y';
}

/** Does an upward drag of `dy` (negative = up) ending at `vy` mean "ask"? About 25% of the screen height, or a quick upward flick. */
export function askOutcome(dy: number, vy: number, height: number): boolean {
  'worklet';
  return dy <= -height * ASK_FRACTION || (vy <= -FLICK_VELOCITY && dy <= -MIN_FLICK_TRAVEL);
}

/** 0..1: how far along an "ask" the upward drag is (for the ASK label). Only upward counts. */
export function askProgress(dy: number, height: number): number {
  'worklet';
  const p = -dy / (height * ASK_FRACTION);
  return p <= 0 ? 0 : p >= 1 ? 1 : p;
}
