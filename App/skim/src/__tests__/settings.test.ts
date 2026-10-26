import { starExpiryLabel } from '@/lib/format';
import { dropFromList, restoredByPendingOps, unstarredByPendingOps } from '@/lib/deck-cache';
import {
  applyPatch, DEFAULTS, formatHours, fromMinutes, HISTORY_CHOICES, parseSettings, quietWindow, stepHistory, stepHours, stepTime, toggleType, toMinutes, EVENT_TYPES,
} from '@/lib/settings';

describe('parseSettings', () => {
  test('nothing at all gives the defaults', () => {
    for (const raw of [undefined, null, {}, [], 'x', 5]) expect(parseSettings(raw)).toEqual(DEFAULTS);
  });
  test('what the server sent is kept', () => {
    const s = parseSettings({ history_limit: 80, urgent_only: true, important_types: ['lab', 'quiz'], quiet_hours: { enabled: false, start: '22:30', end: '06:00' },
      reminders: { day_before_time: '21:00', same_day_hours: 4.5, earliest: '08:00', auto_for_important: false } });
    expect(s).toMatchObject({ history_limit: 80, urgent_only: true, important_types: ['quiz', 'lab'] }); // canonical order
    expect(s.quiet_hours).toEqual({ enabled: false, start: '22:30', end: '06:00' });
    expect(s.reminders).toEqual({ day_before_time: '21:00', same_day_hours: 4.5, earliest: '08:00', auto_for_important: false });
  });
  test('a bad field falls back by itself and never takes the others down', () => {
    const s = parseSettings({ history_limit: 'lots', urgent_only: 'yes', important_types: 'quiz', quiet_hours: { start: '25:00', end: 7 }, reminders: { same_day_hours: 0, earliest: 'noon' } });
    expect(s).toEqual(DEFAULTS);
    const t = parseSettings({ history_limit: 5000, important_types: ['quiz', 'party', 7], reminders: { same_day_hours: 1.3 } });
    expect(t.history_limit).toBe(500);
    expect(t.important_types).toEqual(['quiz']);
    expect(t.reminders.same_day_hours).toBe(5);
    expect(parseSettings({ history_limit: 1 }).history_limit).toBe(10);
    expect(parseSettings({ history_limit: 12.5 }).history_limit).toBe(50);
  });
  test('the time zone is always India (the only one supported)', () => {
    expect(parseSettings({ timezone: 'Europe/London' }).timezone).toBe('Asia/Kolkata');
  });
  test('the result never shares objects with the defaults', () => {
    const s = parseSettings({});
    s.important_types.push('lab');
    s.quiet_hours.start = '01:00';
    expect(DEFAULTS.important_types).toEqual(['quiz', 'exam', 'assignment']);
    expect(DEFAULTS.quiet_hours.start).toBe('23:00');
  });
});

test('applyPatch merges nested objects like the server and keeps everything else', () => {
  const next = applyPatch(DEFAULTS, { quiet_hours: { start: '22:00' }, urgent_only: true, important_types: ['exam'] });
  expect(next.quiet_hours).toEqual({ enabled: true, start: '22:00', end: '07:00' });
  expect(next).toMatchObject({ urgent_only: true, important_types: ['exam'], history_limit: 50 });
  expect(DEFAULTS.quiet_hours.start).toBe('23:00'); // the input is untouched
  expect(applyPatch(DEFAULTS, { reminders: { auto_for_important: false } }).reminders).toEqual({ ...DEFAULTS.reminders, auto_for_important: false });
});

describe('toggleType', () => {
  test('ticking adds in the canonical order, unticking removes', () => {
    expect(toggleType(['quiz', 'exam'], 'lab')).toEqual(['quiz', 'exam', 'lab']);
    expect(toggleType(['exam', 'quiz'], 'assignment')).toEqual(['quiz', 'exam', 'assignment']);
    expect(toggleType(['quiz', 'exam'], 'quiz')).toEqual(['exam']);
    expect(toggleType([], 'other')).toEqual(['other']);
    expect(toggleType(['other'], 'other')).toEqual([]);
  });
  test('an unknown type changes nothing and is never produced; duplicates and junk in the input are cleaned', () => {
    expect(toggleType(['quiz'], 'party')).toEqual(['quiz']);
    expect(toggleType(['quiz', 'quiz', 'x'], 'party')).toEqual(['quiz']);
  });
  test('all nine can be on at once', () => {
    let t: string[] = [];
    for (const e of [...EVENT_TYPES].reverse()) t = toggleType(t, e);
    expect(t).toEqual([...EVENT_TYPES]);
  });
});

describe('time steppers', () => {
  test('minutes <-> HH:MM, wrapping around midnight both ways', () => {
    expect(toMinutes('07:30')).toBe(450);
    expect(fromMinutes(450)).toBe('07:30');
    expect(stepTime('23:45', 30)).toBe('00:15');
    expect(stepTime('00:10', -30)).toBe('23:40');
    expect(stepTime('12:00', 0)).toBe('12:00');
    expect(stepTime('12:00', 1440)).toBe('12:00');
    expect(fromMinutes(-1)).toBe('23:59');
    expect(fromMinutes(1440 * 3 + 5)).toBe('00:05');
  });
  test('every step result is a valid HH:MM the server accepts', () => {
    for (let t = '00:00', i = 0; i < 400; i++, t = stepTime(t, 15)) expect(t).toMatch(/^([01]\d|2[0-3]):[0-5]\d$/);
  });
  test('hours move in halves and stay within 0.5 to 24', () => {
    expect(stepHours(5, 1)).toBe(5.5);
    expect(stepHours(5, -1)).toBe(4.5);
    expect(stepHours(0.5, -1)).toBe(0.5);
    expect(stepHours(24, 1)).toBe(24);
    expect(formatHours(0.5)).toBe('30 minutes');
    expect(formatHours(1)).toBe('1 hour');
    expect(formatHours(5)).toBe('5 hours');
    expect(formatHours(4.5)).toBe('4.5 hours');
  });
  test('history sizes step through the choices and stay within 10 to 500', () => {
    expect(stepHistory(50, 1)).toBe(100);
    expect(stepHistory(50, -1)).toBe(25);
    expect(stepHistory(500, 1)).toBe(500);
    expect(stepHistory(10, -1)).toBe(10);
    expect(stepHistory(60, 1)).toBe(100);   // an odd current value lands on the next choice
    expect(stepHistory(60, -1)).toBe(50);
    for (const c of HISTORY_CHOICES) expect(c).toBeGreaterThanOrEqual(10);
  });
  test('the quiet window reads naturally', () => expect(quietWindow(DEFAULTS.quiet_hours)).toBe('23:00 to 07:00'));
});

describe('starExpiryLabel', () => {
  const NOW = new Date('2026-10-26T12:00:00+05:30');
  const after = (ms: number) => new Date(NOW.getTime() + ms).toISOString();
  const H = 3_600_000, D = 86_400_000;
  test.each([
    [null, null], [undefined, null], ['garbage', null],
    [after(-1000), 'expired'], [after(10 * 60_000), 'less than an hour left'], [after(H), '1 h left'], [after(5 * H + 1000), '5 h left'], [after(23 * H), '23 h left'],
    [after(D), '1 day left'], [after(D + 1), '2 days left'], [after(2 * D), '2 days left'], [after(6 * D), '6 days left'], [after(6 * D + 1), 'until Sun 1 Nov'],
  ])('%s -> %s', (iso, label) => expect(starExpiryLabel(iso as string, NOW)).toBe(label));
  test('a week or more away reads as a date in India', () => expect(starExpiryLabel('2026-11-09T09:00:00+05:30', NOW)).toBe('until Mon 9 Nov'));
});

describe('list edits while changes are still in the outbox', () => {
  test('dropFromList removes one card and leaves the rest and the input alone', () => {
    const data = { cards: [{ id: 1 }, { id: 2 }, { id: 3 }] };
    expect(dropFromList(data, 2)).toEqual({ cards: [{ id: 1 }, { id: 3 }] });
    expect(data.cards).toHaveLength(3);
    expect(dropFromList(undefined, 1)).toBeUndefined();
  });
  test('cards being put back, and stars being removed, are found from the queued operations', () => {
    const ops = [
      { key: 'state:4', method: 'POST', body: { action: 'restore' } }, { key: 'state:5', method: 'POST', body: { action: 'dismiss' } },
      { key: 'state:x', method: 'POST', body: { action: 'restore' } }, { key: 'star:7', method: 'DELETE' }, { key: 'star:8', method: 'PUT', body: { temp: null } },
      { key: 'settings', method: 'PATCH', body: {} },
    ];
    expect([...restoredByPendingOps(ops)]).toEqual([4]);
    expect([...unstarredByPendingOps(ops)]).toEqual([7]);
  });
});
