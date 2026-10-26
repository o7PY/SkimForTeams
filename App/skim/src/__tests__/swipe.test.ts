import { createSwipeGate, MAX_TILT, stampProgress, swipeOutcome, tilt } from '@/lib/swipe';

const W = 400; // 35% = 140

test.each([
  [-150, 0, 'dismiss'], [-140, 0, 'dismiss'], [-139, 0, 'none'], [150, 0, 'open'], [140, 0, 'open'], [139, 0, 'none'], [0, 0, 'none'],
])('slow drag of %i px -> %s', (tx, vx, out) => expect(swipeOutcome(tx, vx, W)).toBe(out));

test.each([
  [-40, -900, 'dismiss'], [40, 900, 'open'], [-24, -800, 'dismiss'], [24, 800, 'open'],
  [-40, -799, 'none'], [40, 799, 'none'],
  [-10, -2000, 'none'], [10, 2000, 'none'], // a twitch that barely moved is not a swipe
  [60, -2000, 'none'], [-60, 2000, 'none'], // the finger travelled one way but the flick went the other: do nothing
])('flick: moved %i px at %i px/s -> %s', (tx, vx, out) => expect(swipeOutcome(tx, vx, W)).toBe(out));

test('the threshold scales with the screen width', () => {
  expect(swipeOutcome(-106, 0, 300)).toBe('dismiss');
  expect(swipeOutcome(-106, 0, 400)).toBe('none');
});

test('stamp progress ramps 0..1 toward its own side only', () => {
  expect(stampProgress(-70, W, -1)).toBeCloseTo(0.5);
  expect(stampProgress(-70, W, 1)).toBe(0);
  expect(stampProgress(70, W, 1)).toBeCloseTo(0.5);
  expect(stampProgress(500, W, 1)).toBe(1);
  expect(stampProgress(0, W, 1)).toBe(0);
});

test('tilt follows the drag and never exceeds 8 degrees', () => {
  expect(tilt(0, W)).toBe(0);
  expect(tilt(200, W)).toBeCloseTo(5);
  expect(tilt(-200, W)).toBeCloseTo(-5);
  expect(tilt(5000, W)).toBe(MAX_TILT);
  expect(tilt(-5000, W)).toBe(-MAX_TILT);
  expect(MAX_TILT).toBeLessThanOrEqual(8);
});

test('a flick commits with far less travel than a slow drag needs', () => {
  expect(swipeOutcome(-30, -1200, W)).toBe('dismiss'); // 7.5% of the width, fast
  expect(swipeOutcome(-30, -100, W)).toBe('none'); // the same distance, slowly
});

describe('the swipe gate', () => {
  test('lets a card through once, however many times it lands', () => {
    const gate = createSwipeGate();
    expect(gate.take(1, 0)).toBe(true);
    expect(gate.take(1, 10)).toBe(false);
    expect(gate.take(1, 500)).toBe(false);
  });
  test('lets the next card through straight away, and the same card again after the hold or a reset (undo)', () => {
    const gate = createSwipeGate(1000);
    expect(gate.take(1, 0)).toBe(true);
    expect(gate.take(2, 5)).toBe(true);
    expect(gate.take(2, 1006)).toBe(true); // a card that stayed on top is not stuck
    gate.reset();
    expect(gate.take(2, 1007)).toBe(true); // put back by Undo, swiped again
  });
});
