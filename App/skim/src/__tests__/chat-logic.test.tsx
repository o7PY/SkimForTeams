// The rules behind the chat sheet and the swipe-up: the gesture's axis lock and threshold, what a citation chip does, the history that
// is sent, and a friendly message for every failure.
import { ApiError } from '@/lib/api';
import { act, create } from 'react-test-renderer';

import { ANCHORED_STARTERS, chatErrorMessage, citationAction, HISTORY_TURNS, OPEN_STARTERS, suggestions, toHistory, useChatEnabled, type ChatTurn } from '@/lib/chat';
import { askOutcome, askProgress, ASK_FRACTION, AXIS_LOCK_PX, lockAxis, swipeOutcome } from '@/lib/swipe';

jest.mock('@react-native-async-storage/async-storage', () => jest.requireActual('@react-native-async-storage/async-storage/jest/async-storage-mock'));
jest.mock('@/lib/auth', () => ({ useAuth: () => ({ api: {} }) }));
let mockMe: { data?: { features?: { chat?: boolean } } } = {};
jest.mock('@/lib/queries', () => ({ useMe: () => mockMe }));

describe('the gesture axis lock', () => {
  test('nothing is decided until the finger has moved about 10 px', () => {
    expect(AXIS_LOCK_PX).toBe(10);
    expect(lockAxis(0, 0)).toBeNull();
    expect(lockAxis(9, -9)).toBeNull();
    expect(lockAxis(-9.9, 0)).toBeNull();
  });
  test('then the dominant axis wins, whichever way it points', () => {
    expect(lockAxis(12, -3)).toBe('x');
    expect(lockAxis(-30, 5)).toBe('x');
    expect(lockAxis(2, -14)).toBe('y');
    expect(lockAxis(-4, 40)).toBe('y');
  });
  test('a diagonal goes to the larger side, and an exact tie to sideways (the existing swipe)', () => {
    expect(lockAxis(20, -21)).toBe('y');
    expect(lockAxis(21, -20)).toBe('x');
    expect(lockAxis(15, -15)).toBe('x');
  });
  test('a mostly sideways drag that wobbles up is still a sideways swipe, and the old left/right rules are untouched', () => {
    expect(lockAxis(-120, -40)).toBe('x');
    expect(swipeOutcome(-150, 0, 400)).toBe('dismiss');
    expect(swipeOutcome(150, 0, 400)).toBe('open');
    expect(swipeOutcome(0, 0, 400)).toBe('none');
  });
});

describe('swipe up = ask', () => {
  const H = 800; // 25% = 200
  test('about 25% of the screen height, dragged up, asks', () => {
    expect(ASK_FRACTION).toBe(0.25);
    expect(askOutcome(-200, 0, H)).toBe(true);
    expect(askOutcome(-199, 0, H)).toBe(false);
    expect(askOutcome(-320, 0, H)).toBe(true);
  });
  test('a quick upward flick asks after a short pull', () => {
    expect(askOutcome(-40, -900, H)).toBe(true);
    expect(askOutcome(-24, -800, H)).toBe(true);
    expect(askOutcome(-40, -799, H)).toBe(false);
    expect(askOutcome(-10, -3000, H)).toBe(false); // a twitch that barely moved
  });
  test('downward and sideways never ask', () => {
    expect(askOutcome(300, 2000, H)).toBe(false);
    expect(askOutcome(0, 0, H)).toBe(false);
    expect(askOutcome(-30, 900, H)).toBe(false); // finger went up but the flick went down
  });
  test('the ASK label ramps 0..1 with the upward drag only', () => {
    expect(askProgress(-100, H)).toBeCloseTo(0.5);
    expect(askProgress(-500, H)).toBe(1);
    expect(askProgress(100, H)).toBe(0);
    expect(askProgress(0, H)).toBe(0);
  });
});

describe('citation chips', () => {
  test('a source with a card opens the card', () => {
    expect(citationAction({ item_id: 42 })).toEqual({ kind: 'card', itemId: 42 });
    expect(citationAction({ item_id: 42, link: 'https://teams.cloud.microsoft/l/message/x' })).toEqual({ kind: 'card', itemId: 42 });
  });
  test('without a card it opens in Teams (or the file), https only', () => {
    expect(citationAction({ link: 'https://teams.cloud.microsoft/l/message/x' })).toEqual({ kind: 'link', url: 'https://teams.cloud.microsoft/l/message/x' });
    expect(citationAction({ item_id: 0, link: 'https://example.com/f.pdf' })).toEqual({ kind: 'link', url: 'https://example.com/f.pdf' });
  });
  test('anything unsafe or missing is just a label', () => {
    expect(citationAction({})).toEqual({ kind: 'none' });
    expect(citationAction({ link: 'http://insecure.example' })).toEqual({ kind: 'none' });
    expect(citationAction({ link: 'javascript:alert(1)' })).toEqual({ kind: 'none' });
    expect(citationAction({ item_id: -3 })).toEqual({ kind: 'none' });
    expect(citationAction({ item_id: 1.5 })).toEqual({ kind: 'none' });
  });
});

describe('starters and history', () => {
  test('anchored and open starter questions', () => {
    expect(suggestions(true)).toEqual(ANCHORED_STARTERS);
    expect(ANCHORED_STARTERS).toEqual(['When is this?', 'Has this changed?', 'What do I need to submit?']);
    expect(suggestions(false)).toEqual(OPEN_STARTERS);
    expect(OPEN_STARTERS).toEqual(['What’s due this week?', 'When’s the next quiz?']);
  });
  const turn = (i: number, over: Partial<ChatTurn> = {}): ChatTurn => ({ id: String(i), role: i % 2 ? 'assistant' : 'user', text: `t${i}`, ...over });
  test('only the last six turns are sent, in order', () => {
    const turns = Array.from({ length: 10 }, (_, i) => turn(i));
    const h = toHistory(turns);
    expect(HISTORY_TURNS).toBe(6);
    expect(h.map((t) => t.text)).toEqual(['t4', 't5', 't6', 't7', 't8', 't9']);
    expect(h[0]).toEqual({ role: 'user', text: 't4' });
  });
  test('the app’s own notices and empty turns are never sent', () => {
    const h = toHistory([turn(0), turn(1, { notice: true, text: 'No connection' }), turn(2, { text: '   ' }), turn(3)]);
    expect(h.map((t) => t.text)).toEqual(['t0', 't3']);
  });
});

describe('friendly errors', () => {
  const api = (status: number, body: Record<string, unknown> | null = null, msg?: string) => new ApiError(status, body, msg);
  test('429 uses the server’s own friendly sentence, or a default', () => {
    expect(chatErrorMessage(api(429, { error: 'You’ve asked a lot of questions this hour. Please try again in a little while.' }))).toContain('this hour');
    expect(chatErrorMessage(api(429))).toMatch(/try again/i);
  });
  test('503 says the assistant is off right now', () => {
    expect(chatErrorMessage(api(503, { error: 'x' }))).toBe('The course assistant isn’t available right now.');
  });
  test('busy, slow, offline, bad request and the rest', () => {
    expect(chatErrorMessage(api(502, { error: 'The course assistant is busy right now. Please try again in a minute.' }))).toContain('busy');
    expect(chatErrorMessage(api(504, null))).toMatch(/busy|try again/i);
    expect(chatErrorMessage(api(0, null, 'The request timed out'))).toBe('That took too long. Please try again.');
    expect(chatErrorMessage(api(0, null, 'No connection'))).toMatch(/No connection/);
    expect(chatErrorMessage(api(400))).toMatch(/differently/);
    expect(chatErrorMessage(api(401))).toMatch(/sign in/);
    expect(chatErrorMessage(api(500))).toMatch(/Something went wrong/);
    expect(chatErrorMessage(new Error('boom'))).toMatch(/Something went wrong/);
  });
});

describe('the on/off switch', () => {
  const enabled = () => {
    let out: boolean | undefined;
    function Probe() {
      out = useChatEnabled();
      return null;
    }
    act(() => { create(<Probe />); });
    return out;
  };
  test('the chat shows only when the server says it is on', () => {
    mockMe = { data: { features: { chat: true } } };
    expect(enabled()).toBe(true);
  });
  test('off, unknown, or an older server that has no features at all: hidden', () => {
    mockMe = { data: { features: { chat: false } } };
    expect(enabled()).toBe(false);
    mockMe = { data: {} };
    expect(enabled()).toBe(false);
    mockMe = {};
    expect(enabled()).toBe(false);
  });
});
