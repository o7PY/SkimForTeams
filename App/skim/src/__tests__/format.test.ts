import { cardBody, cardTitle, changeSummary, clip, docActionLabel, eventLine, eventWhen, formatDay, formatTime, formatWhen, hasUnconfirmed, importantEvent, parseWall, relativeTime } from '@/lib/format';

import { changeCard, documentCard, event, messageCard } from './fixtures';

test('wall-clock fields are read from the string, not the phone’s time zone', () => {
  expect(parseWall('2026-11-02T10:00:00+05:30')).toEqual({ y: 2026, m: 11, d: 2, hh: 10, mm: 0 });
  expect(parseWall('2026-11-02')).toEqual({ y: 2026, m: 11, d: 2, hh: null, mm: null });
  expect(parseWall('nope')).toBeNull();
  expect(parseWall(null)).toBeNull();
  expect(parseWall(undefined)).toBeNull();
});

test.each([
  ['2026-11-02', 'Mon 2 Nov'], ['2026-11-02T10:00:00+05:30', 'Mon 2 Nov'], ['2026-12-31', 'Thu 31 Dec'], ['2027-01-01', 'Fri 1 Jan'],
  ['2028-02-29', 'Tue 29 Feb'], ['2026-10-25T23:59:00+05:30', 'Sun 25 Oct'],
])('formatDay(%s) = %s', (iso, out) => expect(formatDay(iso)).toBe(out));

test('time and when', () => {
  expect(formatTime('2026-11-02T10:05:00+05:30')).toBe('10:05');
  expect(formatTime('2026-11-02T00:00:00+05:30')).toBe('00:00');
  expect(formatTime('2026-11-02')).toBeNull();
  expect(formatWhen('2026-11-02T10:00:00+05:30')).toBe('Mon 2 Nov, 10:00');
  expect(formatWhen('2026-11-02')).toBe('Mon 2 Nov');
  expect(formatWhen('garbage')).toBeNull();
});

test('event time lines: ranges on the same day, start only otherwise', () => {
  expect(eventWhen({ start: '2026-11-02T10:00:00+05:30', end: '2026-11-02T11:30:00+05:30' })).toBe('Mon 2 Nov, 10:00–11:30');
  expect(eventWhen({ start: '2026-11-02T22:00:00+05:30', end: '2026-11-03T01:00:00+05:30' })).toBe('Mon 2 Nov, 22:00');
  expect(eventWhen({ start: '2026-11-02', end: null })).toBe('Mon 2 Nov');
  expect(eventWhen({ start: null, end: null })).toBeNull();
  expect(eventLine(event())).toBe('Quiz 1 · Mon 2 Nov, 10:00 · LHC 104');
  expect(eventLine(event({ venue: null, start: null }))).toBe('Quiz 1');
});

describe('relativeTime (IST calendar days)', () => {
  const now = new Date('2026-10-26T12:00:00+05:30');
  test.each([
    ['2026-10-26T11:59:40+05:30', 'just now'], ['2026-10-26T11:55:00+05:30', '5 min ago'], ['2026-10-26T11:01:00+05:30', '59 min ago'],
    ['2026-10-26T09:00:00+05:30', '3 h ago'], ['2026-10-25T23:30:00+05:30', 'Yesterday'], ['2026-10-25T05:00:00+05:30', 'Yesterday'],
    ['2026-10-24T20:00:00+05:30', '2 d ago'], ['2026-10-20T10:00:00+05:30', '6 d ago'], ['2026-10-19T10:00:00+05:30', '19 Oct'],
    ['2025-12-31T10:00:00+05:30', '31 Dec 2025'], ['2026-10-26T12:30:00+05:30', 'just now'], ['not a date', ''],
  ])('%s -> %s', (iso, out) => expect(relativeTime(iso, now)).toBe(out));

  test('"Yesterday" follows the Indian calendar even when the phone is elsewhere', () => {
    // 20:00 UTC on the 25th is 01:30 IST on the 26th; one hour earlier than "now" in UTC terms but the same IST day
    expect(relativeTime('2026-10-25T20:00:00Z', new Date('2026-10-26T06:00:00Z'))).toBe('10 h ago');
    expect(relativeTime('2026-10-25T17:00:00Z', new Date('2026-10-26T12:00:00Z'))).toBe('Yesterday'); // 22:30 IST on the 25th vs 17:30 IST on the 26th
  });
});

describe('change summaries', () => {
  const c = (type: string, before: object | null, after: object | null) => ({ type, before, after } as NonNullable<ReturnType<typeof changeCard>['change']>);
  test('moves show from -> to', () => {
    expect(changeSummary(changeCard().change!)).toEqual({ label: 'Moved', from: 'Fri 30 Oct, 10:00', to: 'Mon 2 Nov, 10:00' });
  });
  test('venue, time, date, cancel, back on, details', () => {
    expect(changeSummary(c('venue_changed', { venue: 'LHC 104' }, { venue: 'LHC 201' }))).toEqual({ label: 'New venue', from: 'LHC 104', to: 'LHC 201' });
    expect(changeSummary(c('time_set', { start: '2026-11-02' }, { start: '2026-11-02T10:00:00+05:30' }))).toEqual({ label: 'Time set', from: 'Mon 2 Nov', to: 'Mon 2 Nov, 10:00' });
    expect(changeSummary(c('date_set', null, { start: '2026-11-09' }))).toEqual({ label: 'Date set', from: null, to: 'Mon 9 Nov' });
    expect(changeSummary(c('cancelled', { start: '2026-11-02' }, { status: 'cancelled' }))).toEqual({ label: 'Cancelled', from: null, to: null });
    expect(changeSummary(c('uncancelled', { status: 'cancelled' }, { start: '2026-11-03' })).label).toBe('Back on');
    expect(changeSummary(c('details_changed', null, { details: 'Open book.\n\nBring   notes.' }))).toEqual({ label: 'Updated', from: null, to: 'Open book. Bring notes.' });
  });
  test('an unknown type still renders', () => {
    expect(changeSummary(c('mystery', null, null))).toEqual({ label: 'Updated', from: null, to: null });
    expect(changeSummary({ type: null, before: null, after: null }).label).toBe('Updated');
  });
});

describe('titles and bodies', () => {
  test('a message with a subject uses it; the body is the whole text', () => {
    const m = messageCard({ message: { ...messageCard().message!, subject: 'Quiz 1 details', text: 'Monday 10:00.' } });
    expect(cardTitle(m)).toBe('Quiz 1 details');
    expect(cardBody(m)).toBe('Monday 10:00.');
  });
  test('a message without a subject uses its first line, and the body is the rest', () => {
    const m = messageCard();
    expect(cardTitle(m)).toBe('Quiz 1 is on Monday');
    expect(cardBody(m)).toBe('Bring a calculator and your ID card.');
  });
  test('leading blank lines are skipped; a one-line message has no body', () => {
    const m = messageCard({ message: { ...messageCard().message!, text: '\n\n  Hello class  ' } });
    expect(cardTitle(m)).toBe('Hello class');
    expect(cardBody(m)).toBe('');
  });
  test('a long first line is clipped', () => {
    const m = messageCard({ message: { ...messageCard().message!, text: 'word '.repeat(60) } });
    expect(cardTitle(m).length).toBeLessThanOrEqual(90);
    expect(cardTitle(m).endsWith('…')).toBe(true);
  });
  test('an empty message falls back to its event or a generic title', () => {
    const none = messageCard({ message: { ...messageCard().message!, text: '' } });
    expect(cardTitle(none)).toBe('Post');
    expect(cardTitle({ ...none, events: [event()] })).toBe('Quiz 1');
  });
  test('a deleted message shows nothing of its content', () => {
    const d = messageCard({ message: { ...messageCard().message!, deleted: true, text: '' } });
    expect(cardTitle(d)).toBe('Deleted in Teams');
    expect(cardBody(d)).toBe('');
  });
  test('files and changes', () => {
    expect(cardTitle(documentCard())).toBe('Syllabus v2.pdf');
    expect(cardBody(documentCard())).toBe('File renamed from “Syllabus.pdf”');
    expect(cardBody(documentCard({ document: { ...documentCard().document!, action: 'created', old_name: null } }))).toBe('New file');
    expect(docActionLabel('weird')).toBe('File');
    expect(docActionLabel(null)).toBe('File');
    expect(cardTitle(changeCard())).toBe('Quiz 1');
    expect(cardTitle(changeCard({ events: [] }))).toBe('Date change');
    expect(cardBody(changeCard())).toBe('');
  });
});

test('clip collapses whitespace and cuts on a word-ish boundary with an ellipsis', () => {
  expect(clip('a   b\n\nc', 50)).toBe('a b c');
  expect(clip('abcdefghij', 5)).toBe('abcd…');
});

test('importance and unconfirmed flags', () => {
  expect(importantEvent(messageCard({ events: [event({ important: false }), event({ id: 2, important: true })] }))?.id).toBe(2);
  expect(importantEvent(messageCard({ events: [event({ status: 'cancelled' })] }))).toBeUndefined(); // a cancelled quiz is not urgent any more
  expect(importantEvent(messageCard())).toBeUndefined();
  expect(hasUnconfirmed(messageCard({ events: [event({ unconfirmed: true })] }))).toBe(true);
  expect(hasUnconfirmed(messageCard({ events: [event()] }))).toBe(false);
});
