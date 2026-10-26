import { stampProgress, swipeOutcome, tilt } from '@/lib/swipe';

const W = 400; // 30% = 120

test.each([
  [-130, 0, 'dismiss'], [-120, 0, 'dismiss'], [-119, 0, 'none'], [130, 0, 'open'], [120, 0, 'open'], [119, 0, 'none'], [0, 0, 'none'],
])('slow drag of %i px -> %s', (tx, vx, out) => expect(swipeOutcome(tx, vx, W)).toBe(out));

test.each([
  [-40, -900, 'dismiss'], [40, 900, 'open'], [-24, -800, 'dismiss'], [24, 800, 'open'],
  [-40, -799, 'none'], [40, 799, 'none'],
  [-10, -2000, 'none'], [10, 2000, 'none'], // a twitch that barely moved is not a swipe
  [60, -2000, 'none'], [-60, 2000, 'none'], // the finger travelled one way but the flick went the other: do nothing
])('flick: moved %i px at %i px/s -> %s', (tx, vx, out) => expect(swipeOutcome(tx, vx, W)).toBe(out));

test('the threshold scales with the screen width', () => {
  expect(swipeOutcome(-100, 0, 300)).toBe('dismiss');
  expect(swipeOutcome(-100, 0, 400)).toBe('none');
});

test('stamp progress ramps 0..1 toward its own side only', () => {
  expect(stampProgress(-60, W, -1)).toBeCloseTo(0.5);
  expect(stampProgress(-60, W, 1)).toBe(0);
  expect(stampProgress(60, W, 1)).toBeCloseTo(0.5);
  expect(stampProgress(500, W, 1)).toBe(1);
  expect(stampProgress(0, W, 1)).toBe(0);
});

test('tilt follows the drag and stays within 14 degrees', () => {
  expect(tilt(0, W)).toBe(0);
  expect(tilt(200, W)).toBeCloseTo(7);
  expect(tilt(-200, W)).toBeCloseTo(-7);
  expect(tilt(5000, W)).toBe(14);
  expect(tilt(-5000, W)).toBe(-14);
});
