import {
  absolutePresets, calendarEventFor, calendarIntentExtras, composeAbsolute, dayChoices, defaultPickerTime, fileUrl, istInstant, istParts, newUuid,
  offsetPresets, safeUrl, snoozeOptions, startMs, starLabel, tempStarOptions, toIstIso, upcomingEvent,
} from '@/lib/actions';

import { addToCalendarWith } from '@/lib/calendar';
import { isPending, pendingRecommended, recommendedAlreadySet, withoutReminder, withoutStar, withReminders, withStar } from '@/lib/item-cache';

import { changeCard, event, messageCard } from './fixtures';

const at = (iso: string) => new Date(iso);
const NOW = at('2026-10-26T12:00:00+05:30'); // Monday noon in India

describe('IST arithmetic', () => {
  test('toIstIso / istParts / istInstant agree and survive month and year rollover', () => {
    const ms = istInstant(2026, 12, 31, 23, 30);
    expect(toIstIso(ms)).toBe('2026-12-31T23:30:00+05:30');
    expect(toIstIso(ms + 60 * 60_000)).toBe('2027-01-01T00:30:00+05:30');
    expect(istParts(ms)).toEqual({ y: 2026, m: 12, d: 31, hh: 23, mm: 30 });
    expect(toIstIso(istInstant(2026, 2, 29, 9, 0))).toBe('2026-03-01T09:00:00+05:30'); // 2026 is not a leap year: day 29 rolls over
    expect(toIstIso(istInstant(2026, 10, 32, 9, 0))).toBe('2026-11-01T09:00:00+05:30');
  });
  test('an instant late in the UTC day is already tomorrow in India', () => {
    expect(istParts(at('2026-10-26T20:00:00Z').getTime())).toMatchObject({ d: 27, hh: 1, mm: 30 });
  });
});

describe('snooze options (P4-D-115)', () => {
  const card = { recommended_reminders: [{ kind: 'recommended_same_day', event_id: 1, fire_at: '2026-11-02T07:00:00+05:30' }] };
  test('at noon: 1 hour, tonight 20:00, tomorrow 09:00, and before the event', () => {
    const o = snoozeOptions(NOW, card);
    expect(o.map((x) => x.id)).toEqual(['hour', 'tonight', 'tomorrow', 'event']);
    expect(o.map((x) => x.until)).toEqual(['2026-10-26T13:00:00+05:30', '2026-10-26T20:00:00+05:30', '2026-10-27T09:00:00+05:30', '2026-11-02T07:00:00+05:30']);
    expect(o[1].detail).toBe('20:00');
  });
  test('"tonight" disappears when it is less than 15 minutes away or already past', () => {
    expect(snoozeOptions(at('2026-10-26T19:50:00+05:30'), {}).map((x) => x.id)).toEqual(['hour', 'tomorrow']);
    expect(snoozeOptions(at('2026-10-26T19:44:00+05:30'), {}).map((x) => x.id)).toContain('tonight');
    expect(snoozeOptions(at('2026-10-26T22:00:00+05:30'), {}).map((x) => x.id)).toEqual(['hour', 'tomorrow']);
  });
  test('"tomorrow" is tomorrow in INDIA, even late at night, and across a month end', () => {
    expect(snoozeOptions(at('2026-10-26T23:50:00+05:30'), {}).find((x) => x.id === 'tomorrow')!.until).toBe('2026-10-27T09:00:00+05:30');
    expect(snoozeOptions(at('2026-10-31T21:00:00+05:30'), {}).find((x) => x.id === 'tomorrow')!.until).toBe('2026-11-01T09:00:00+05:30');
    // 20:30 UTC on the 26th is already 02:00 on the 27th in India -> tomorrow is the 28th
    expect(snoozeOptions(at('2026-10-26T20:30:00Z'), {}).find((x) => x.id === 'tomorrow')!.until).toBe('2026-10-28T09:00:00+05:30');
  });
  test('"before the event" needs a recommended same-day time that is still ahead', () => {
    expect(snoozeOptions(NOW, { recommended_reminders: [{ kind: 'recommended_same_day', event_id: 1, fire_at: null }] }).map((x) => x.id)).not.toContain('event');
    expect(snoozeOptions(NOW, { recommended_reminders: [{ kind: 'recommended_same_day', event_id: 1, fire_at: '2026-10-26T12:02:00+05:30' }] }).map((x) => x.id)).not.toContain('event');
    expect(snoozeOptions(NOW, { recommended_reminders: [{ kind: 'recommended_day_before', event_id: 1, fire_at: '2026-11-01T20:00:00+05:30' }] }).map((x) => x.id)).not.toContain('event');
    expect(snoozeOptions(NOW, {}).map((x) => x.id)).not.toContain('event');
  });
  test('every option is within what the server accepts: in the future, with an offset, under 31 days', () => {
    for (const o of snoozeOptions(NOW, card)) {
      const d = new Date(o.until).getTime() - NOW.getTime();
      expect(d).toBeGreaterThan(0);
      expect(d).toBeLessThan(31 * 86_400_000);
      expect(o.until).toMatch(/\+05:30$/);
    }
  });
});

describe('reminder choices', () => {
  const card = messageCard({ events: [event({ start: '2026-11-02T10:00:00+05:30' })] });
  test('offset presets for an event with a time, only those still in the future', () => {
    const { event: e, presets } = offsetPresets(card, NOW);
    expect(e?.id).toBe(1);
    expect(presets.map((p) => p.minutes)).toEqual([15, 30, 60, 120, 1440, 2880]);
    expect(presets[4]).toMatchObject({ label: '1 day before', fires: 'Sun 1 Nov, 10:00' });
    const soon = offsetPresets(card, at('2026-11-02T08:30:00+05:30')).presets.map((p) => p.minutes);
    expect(soon).toEqual([15, 30, 60]); // 2 h before would be 08:00: already past
    expect(offsetPresets(card, at('2026-11-02T09:59:30+05:30')).presets).toEqual([]);
  });
  test('a date-only event, a cancelled event, an over event and no event offer no offsets', () => {
    expect(offsetPresets(messageCard({ events: [event({ start: '2026-11-02' })] }), NOW).presets).toEqual([]);
    expect(offsetPresets(messageCard({ events: [event({ status: 'cancelled' })] }), NOW).presets).toEqual([]);
    expect(offsetPresets(messageCard({ events: [event({ start: '2026-10-20T10:00:00+05:30' })] }), NOW).presets).toEqual([]);
    expect(offsetPresets(messageCard(), NOW).presets).toEqual([]);
  });
  test('upcomingEvent: a date-only event counts as upcoming all of its day', () => {
    const c = messageCard({ events: [event({ start: '2026-10-26' })] });
    expect(upcomingEvent(c, at('2026-10-26T23:00:00+05:30'))?.id).toBe(1);
    expect(upcomingEvent(c, at('2026-10-27T00:01:00+05:30'))).toBeUndefined();
  });
  test('startMs: null for date-only, a number for a timed start', () => {
    expect(startMs({ start: '2026-11-02' })).toBeNull();
    expect(startMs({ start: null })).toBeNull();
    expect(startMs({ start: '2026-11-02T10:00:00+05:30' })).toBe(new Date('2026-11-02T10:00:00+05:30').getTime());
  });
  test('absolute presets', () => {
    expect(absolutePresets(NOW).map((p) => [p.label, p.at])).toEqual([
      ['In 1 hour', '2026-10-26T13:00:00+05:30'], ['Tonight 20:00', '2026-10-26T20:00:00+05:30'], ['Tomorrow 09:00', '2026-10-27T09:00:00+05:30'],
    ]);
  });
});

describe('the custom time picker', () => {
  test('composeAbsolute builds an IST time and rejects the past and nonsense', () => {
    expect(composeAbsolute(NOW, 0, 18, 30)?.at).toBe('2026-10-26T18:30:00+05:30');
    expect(composeAbsolute(NOW, 3, 7, 5)?.at).toBe('2026-10-29T07:05:00+05:30');
    expect(composeAbsolute(NOW, 0, 11, 0)).toBeNull();   // earlier today
    expect(composeAbsolute(NOW, 0, 12, 0)).toBeNull();   // right now
    expect(composeAbsolute(NOW, 0, 12, 1)?.at).toBe('2026-10-26T12:01:00+05:30'); // a minute away is the earliest
    expect(composeAbsolute(NOW, -1, 12, 0)).toBeNull();
    expect(composeAbsolute(NOW, 61, 12, 0)).toBeNull();
    expect(composeAbsolute(NOW, 1.5, 12, 0)).toBeNull();
    expect(composeAbsolute(NOW, 0, 24, 0)).toBeNull();
    expect(composeAbsolute(NOW, 0, 12, 60)).toBeNull();
  });
  test('the picker’s day strip starts today, labels Today/Tomorrow, and runs 14 days', () => {
    const days = dayChoices(NOW);
    expect(days).toHaveLength(14);
    expect(days.slice(0, 4).map((d) => d.label)).toEqual(['Today', 'Tomorrow', 'Wed 28 Oct', 'Thu 29 Oct']);
    expect(days[13].label).toBe('Sun 8 Nov');
  });
  test('the default time is the next whole hour at least 30 minutes away', () => {
    expect(defaultPickerTime(at('2026-10-26T12:00:00+05:30'))).toEqual({ dayOffset: 0, hh: 13, mm: 0 });
    expect(defaultPickerTime(at('2026-10-26T12:40:00+05:30'))).toEqual({ dayOffset: 0, hh: 14, mm: 0 });
    expect(defaultPickerTime(at('2026-10-26T23:40:00+05:30'))).toEqual({ dayOffset: 1, hh: 1, mm: 0 }); // rolls into tomorrow
    const d = defaultPickerTime(NOW);
    expect(composeAbsolute(NOW, d.dayOffset, d.hh, d.mm)).not.toBeNull();
  });
});

test('newUuid makes distinct v4-shaped ids', () => {
  const ids = new Set(Array.from({ length: 200 }, newUuid));
  expect(ids.size).toBe(200);
  for (const id of ids) expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

describe('stars', () => {
  test('temp star choices: 1/3/7 days always, "until it’s over" only with an upcoming event', () => {
    expect(tempStarOptions(messageCard(), NOW).map((o) => o.id)).toEqual(['1d', '3d', '7d']);
    expect(tempStarOptions(messageCard({ events: [event()] }), NOW).map((o) => o.id)).toEqual(['1d', '3d', '7d', 'event_over']);
    expect(tempStarOptions(messageCard({ events: [event({ start: '2026-10-20T10:00:00+05:30' })] }), NOW).map((o) => o.id)).toEqual(['1d', '3d', '7d']);
    expect(tempStarOptions(messageCard({ events: [event({ status: 'cancelled' })] }), NOW).map((o) => o.id)).toEqual(['1d', '3d', '7d']);
  });
  test('star labels', () => {
    const user = messageCard().user;
    expect(starLabel(messageCard())).toBe('Star');
    expect(starLabel(messageCard({ user: { ...user, starred: true } }))).toBe('Starred');
    expect(starLabel(messageCard({ user: { ...user, starred: true, temp_star_expires_at: '2026-10-29T12:00:00+05:30' } }))).toBe('Starred until Thu 29 Oct, 12:00');
  });
});

describe('add to calendar (P4-D-130)', () => {
  const link = 'https://teams.cloud.microsoft/l/message/x';
  const mk = (e = {}, c = {}) => messageCard({ links: { teams_deep_link: link, file_url: null }, events: [event(e)], ...c });
  test('a timed event: its start, one hour long by default, venue, and the Teams link in the notes', () => {
    const d = calendarEventFor(mk(), NOW)!;
    expect(d).toMatchObject({ title: 'Quiz 1', allDay: false, location: 'LHC 104' });
    expect(d.startDate.toISOString()).toBe('2026-11-02T04:30:00.000Z');
    expect(d.endDate.getTime() - d.startDate.getTime()).toBe(3_600_000);
    expect(d.notes).toContain(link);
    expect(d.notes).toContain('From Skim · MA 221');
  });
  test('an explicit end is used when it is after the start', () => {
    expect(calendarEventFor(mk({ end: '2026-11-02T11:30:00+05:30' }), NOW)!.endDate.toISOString()).toBe('2026-11-02T06:00:00.000Z');
    expect(calendarEventFor(mk({ end: '2026-11-02T09:00:00+05:30' }), NOW)!.endDate.toISOString()).toBe('2026-11-02T05:30:00.000Z'); // bad end: back to 1 h
  });
  test('a deadline is 15 minutes ENDING at the deadline', () => {
    const d = calendarEventFor(mk({ is_deadline: true, start: '2026-11-06T23:59:00+05:30' }), NOW)!;
    expect(d.endDate.toISOString()).toBe('2026-11-06T18:29:00.000Z');
    expect(d.endDate.getTime() - d.startDate.getTime()).toBe(15 * 60_000);
  });
  test('a date-only event is an all-day entry on that day', () => {
    const d = calendarEventFor(mk({ start: '2026-11-02' }), NOW)!;
    expect(d.allDay).toBe(true);
    expect(d.startDate.toISOString()).toBe('2026-11-02T00:00:00.000Z');
    expect(d.endDate.toISOString()).toBe('2026-11-03T00:00:00.000Z');
  });
  test('no venue -> no location; no link -> notes still say where it came from; a cancelled-only card gives nothing', () => {
    expect(calendarEventFor(mk({ venue: null }), NOW)!.location).toBeUndefined();
    expect(calendarEventFor(mk({}, { links: { teams_deep_link: null, file_url: null } }), NOW)!.notes).toBe('From Skim · MA 221');
    expect(calendarEventFor(mk({ status: 'cancelled' }), NOW)).toBeNull();
    expect(calendarEventFor(messageCard(), NOW)).toBeNull();
    expect(calendarEventFor(mk({ start: null }), NOW)).toBeNull();
  });
  test('an event that is over is still offered (someone may want it in their calendar for the record) only when it is the only one', () => {
    expect(calendarEventFor(mk({ start: '2026-10-20T10:00:00+05:30' }), NOW)).not.toBeNull();
  });
  test('a change card adds the event it is about', () => {
    expect(calendarEventFor(changeCard(), NOW)!.title).toBe('Quiz 1');
  });
  test('the Android intent extras carry the same values in the keys Android expects', () => {
    const d = calendarEventFor(mk(), NOW)!;
    expect(calendarIntentExtras(d)).toEqual({ title: 'Quiz 1', beginTime: d.startDate.getTime(), endTime: d.endDate.getTime(), allDay: false, eventLocation: 'LHC 104', description: d.notes });
    expect(calendarIntentExtras(calendarEventFor(mk({ venue: null }), NOW)!)).not.toHaveProperty('eventLocation');
  });
});

describe('links', () => {
  test('only https links are ever opened', () => {
    expect(safeUrl('https://x.example/a')).toBe('https://x.example/a');
    for (const bad of ['http://x', 'javascript:alert(1)', 'file:///etc/passwd', 'intent://x', 'content://x', '', null, undefined]) expect(safeUrl(bad as string)).toBeNull();
  });
  test('the file link: a file card’s own, else the first attachment that has one', () => {
    const none = { name: 'a', document_id: 1, web_url: null };
    const has = { name: 'b', document_id: 2, web_url: 'https://sp/b.pdf' };
    expect(fileUrl(messageCard({ links: { teams_deep_link: null, file_url: 'https://sp/own.pdf' } }))).toBe('https://sp/own.pdf');
    expect(fileUrl(messageCard({ message: { ...messageCard().message!, attachments: [none, has] } }))).toBe('https://sp/b.pdf');
    expect(fileUrl(messageCard())).toBeNull();
  });
});

// ---------------------------------------------------------------- calendar fallback + optimistic edits

describe('addToCalendarWith', () => {
  const draft = calendarEventFor(messageCard({ events: [event()] }), NOW)!;
  const ok = jest.fn(async () => undefined);
  const boom = (name: string) => jest.fn(async () => { throw Object.assign(new Error('x'), { name }); });

  test('expo-calendar first; the intent is not touched when it works', async () => {
    const intent = jest.fn(async () => undefined);
    expect(await addToCalendarWith({ viaExpoCalendar: ok, viaIntent: intent }, draft)).toEqual({ via: 'expo-calendar', opened: true });
    expect(intent).not.toHaveBeenCalled();
    expect(ok).toHaveBeenCalledWith(draft);
  });
  test('if expo-calendar fails, the Android intent is used and that is reported', async () => {
    const intent = jest.fn(async () => undefined);
    expect(await addToCalendarWith({ viaExpoCalendar: boom('PermissionError'), viaIntent: intent }, draft)).toEqual({ via: 'intent', opened: true });
    expect(intent).toHaveBeenCalledWith(draft);
  });
  test('if both fail, the answer says so and names both failures (never their messages)', async () => {
    const r = await addToCalendarWith({ viaExpoCalendar: boom('A'), viaIntent: boom('B') }, draft);
    expect(r).toEqual({ via: 'none', opened: false, reason: 'A / B' });
  });
});

describe('optimistic edits of an opened card', () => {
  const card = messageCard({ events: [event()], recommended_reminders: [
    { kind: 'recommended_day_before', event_id: 1, fire_at: '2026-11-01T20:00:00+05:30' },
    { kind: 'recommended_same_day', event_id: 1, fire_at: '2026-11-02T07:00:00+05:30' },
  ] });
  test('stars', () => {
    expect(withStar(card, null, NOW).user).toMatchObject({ starred: true, temp_star_expires_at: null });
    expect(withStar(card, '3d', NOW).user.temp_star_expires_at).toBe('2026-10-29T06:30:00.000Z');
    expect(withStar(card, 'event_over', NOW).user.starred).toBe(true);
    expect(withoutStar(withStar(card, '1d', NOW)).user).toMatchObject({ starred: false, temp_star_expires_at: null });
    expect(card.user.starred).toBe(false); // the input is never changed
  });
  test('reminders are added once, removed by id, and the recommended pair shows both times', () => {
    const pair = pendingRecommended(card, 'abc');
    expect(pair.map((r) => [r.id, r.kind, r.fire_at])).toEqual([
      ['abc', 'recommended_day_before', '2026-11-01T20:00:00+05:30'], ['abc:pending', 'recommended_same_day', '2026-11-02T07:00:00+05:30'],
    ]);
    const added = withReminders(card, pair);
    expect(added.user.reminders).toHaveLength(2);
    expect(withReminders(added, pair).user.reminders).toHaveLength(2); // adding the same ones again changes nothing
    expect(withoutReminder(added, 'abc').user.reminders.map((r) => r.id)).toEqual(['abc:pending']);
    expect(recommendedAlreadySet(added)).toBe(true);
    expect(recommendedAlreadySet(card)).toBe(false);
    expect(isPending(pair[1])).toBe(true);
    expect(isPending(pair[0])).toBe(false);
  });
  test('a recommended time that is already past is not shown as pending', () => {
    const c = messageCard({ recommended_reminders: [{ kind: 'recommended_day_before', event_id: 1, fire_at: null }, { kind: 'recommended_same_day', event_id: 1, fire_at: '2026-11-02T07:00:00+05:30' }] });
    expect(pendingRecommended(c, 'x').map((r) => r.kind)).toEqual(['recommended_same_day']);
  });
});
