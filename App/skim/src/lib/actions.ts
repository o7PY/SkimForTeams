// What the card's action bar offers and the values it sends (PHASE4.md 7.2 item 4, P4-D-114/115/116/118/130). Pure, so it can be tested
// exhaustively. All calendar days are Asia/Kolkata (P4-D-133): a phone in another time zone must not move "tonight 20:00".
import { clip, formatWhen, parseWall } from './format';
import type { Card, EventChip } from './types';

const IST_MS = 330 * 60_000;
const pad = (n: number) => String(n).padStart(2, '0');

export type IstParts = { y: number; m: number; d: number; hh: number; mm: number };
export const istParts = (ms: number): IstParts => {
  const t = new Date(ms + IST_MS);
  return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate(), hh: t.getUTCHours(), mm: t.getUTCMinutes() };
};
/** The instant (ms) of an IST wall-clock time. Day/month overflow rolls over (day 32 = the 1st of next month). */
export const istInstant = (y: number, m: number, d: number, hh = 0, mm = 0): number => Date.UTC(y, m - 1, d, hh, mm) - IST_MS;
/** "2026-11-02T10:00:00+05:30": the form the API takes and returns. */
export const toIstIso = (ms: number): string => {
  const p = istParts(ms);
  return `${p.y}-${pad(p.m)}-${pad(p.d)}T${pad(p.hh)}:${pad(p.mm)}:00+05:30`;
};

/** An event's start as an instant, or null for a date-only or missing start. */
export function startMs(e: Pick<EventChip, 'start'>): number | null {
  const w = parseWall(e.start);
  return w && w.hh !== null ? istInstant(w.y, w.m, w.d, w.hh, w.mm ?? 0) : null;
}
const dayMs = (e: Pick<EventChip, 'start'>): number | null => {
  const w = parseWall(e.start);
  return w ? istInstant(w.y, w.m, w.d, 23, 59) : null; // the end of the event's day: a date-only event is "upcoming" all that day
};

/** The first event on the card that is still ahead and not cancelled. */
export function upcomingEvent(card: Card, now: Date): EventChip | undefined {
  return card.events.find((e) => e.status !== 'cancelled' && (startMs(e) ?? dayMs(e) ?? -Infinity) > now.getTime());
}

// ---------------------------------------------------------------- snooze (P4-D-115)
export type Snooze = { id: 'hour' | 'tonight' | 'tomorrow' | 'event'; label: string; detail: string; until: string };

export function snoozeOptions(now: Date, card: Pick<Card, 'recommended_reminders'>): Snooze[] {
  const t = now.getTime();
  const p = istParts(t);
  const out: Snooze[] = [{ id: 'hour', label: '1 hour', detail: shortWhen(t + 3_600_000), until: toIstIso(t + 3_600_000) }];
  const tonight = istInstant(p.y, p.m, p.d, 20, 0);
  if (tonight - t >= 15 * 60_000) out.push({ id: 'tonight', label: 'Tonight', detail: '20:00', until: toIstIso(tonight) });
  const tomorrow = istInstant(p.y, p.m, p.d + 1, 9, 0);
  out.push({ id: 'tomorrow', label: 'Tomorrow', detail: shortWhen(tomorrow), until: toIstIso(tomorrow) });
  const same = card.recommended_reminders?.find((r) => r.kind === 'recommended_same_day')?.fire_at;
  const sameMs = same ? new Date(same).getTime() : NaN;
  if (sameMs - t >= 5 * 60_000) out.push({ id: 'event', label: 'Before the event', detail: shortWhen(sameMs), until: toIstIso(sameMs) });
  return out;
}
function shortWhen(ms: number): string {
  return formatWhen(toIstIso(ms)) ?? '';
}

// ---------------------------------------------------------------- reminders (P4-D-116/118)
export const OFFSET_PRESETS = [
  { minutes: 15, label: '15 minutes before' }, { minutes: 30, label: '30 minutes before' }, { minutes: 60, label: '1 hour before' },
  { minutes: 120, label: '2 hours before' }, { minutes: 1440, label: '1 day before' }, { minutes: 2880, label: '2 days before' },
] as const;

/** "Before the event" choices: only for an event with a real start time, and only those that are still in the future. */
export function offsetPresets(card: Card, now: Date) {
  const e = upcomingEvent(card, now);
  const s = e ? startMs(e) : null;
  if (!e || s === null) return { event: e, presets: [] as { minutes: number; label: string; fires: string }[] };
  return {
    event: e,
    presets: OFFSET_PRESETS.filter((o) => s - o.minutes * 60_000 > now.getTime() + 60_000).map((o) => ({ ...o, fires: shortWhen(s - o.minutes * 60_000) })),
  };
}

export type AbsolutePreset = { id: string; label: string; detail: string; at: string };
export function absolutePresets(now: Date): AbsolutePreset[] {
  const s = snoozeOptions(now, {});
  return s.filter((o) => o.id !== 'event').map((o) => ({ id: o.id, label: o.id === 'hour' ? 'In 1 hour' : o.id === 'tonight' ? 'Tonight 20:00' : 'Tomorrow 09:00', detail: o.detail, at: o.until }));
}

export const MAX_DAYS_AHEAD = 60;
export const dayChoices = (now: Date, n = 14) => {
  const p = istParts(now.getTime());
  return Array.from({ length: n }, (_, i) => {
    const ms = istInstant(p.y, p.m, p.d + i, 12, 0);
    return { offset: i, label: i === 0 ? 'Today' : i === 1 ? 'Tomorrow' : (formatWhen(toIstIso(ms))?.split(',')[0] ?? ''), ms };
  });
};

/** A custom reminder time: day `dayOffset` from today (IST) at hh:mm. null if it is not at least a minute away. */
export function composeAbsolute(now: Date, dayOffset: number, hh: number, mm: number): { at: string; ms: number } | null {
  if (!Number.isInteger(dayOffset) || dayOffset < 0 || dayOffset > MAX_DAYS_AHEAD || hh < 0 || hh > 23 || mm < 0 || mm > 59) return null;
  const p = istParts(now.getTime());
  const ms = istInstant(p.y, p.m, p.d + dayOffset, hh, mm);
  return ms - now.getTime() >= 60_000 ? { at: toIstIso(ms), ms } : null;
}

/** The next sensible default for the picker: the next whole hour, at least 30 minutes away. */
export function defaultPickerTime(now: Date): { dayOffset: number; hh: number; mm: number } {
  const ms = Math.ceil((now.getTime() + IST_MS + 30 * 60_000) / 3_600_000) * 3_600_000 - IST_MS; // whole hour on the INDIAN clock (UTC+5:30)
  const p = istParts(ms);
  const today = istParts(now.getTime());
  return { dayOffset: Math.round((istInstant(p.y, p.m, p.d, 12, 0) - istInstant(today.y, today.m, today.d, 12, 0)) / 86_400_000), hh: p.hh, mm: p.mm };
}

export function newUuid(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    return (ch === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

// ---------------------------------------------------------------- stars (P4-D-114)
export type TempStar = '1d' | '3d' | '7d' | 'event_over';
export function tempStarOptions(card: Card, now: Date): { id: TempStar; label: string; detail?: string }[] {
  const out: { id: TempStar; label: string; detail?: string }[] = [{ id: '1d', label: '1 day' }, { id: '3d', label: '3 days' }, { id: '7d', label: '7 days' }];
  const e = upcomingEvent(card, now);
  if (e) out.push({ id: 'event_over', label: 'Until it’s over', detail: e.title });
  return out;
}

export function starLabel(card: Card): string {
  if (!card.user.starred) return 'Star';
  return card.user.temp_star_expires_at ? `Starred until ${formatWhen(card.user.temp_star_expires_at) ?? 'later'}` : 'Starred';
}

// ---------------------------------------------------------------- calendar (P4-D-130)
export type CalendarEventDraft = { title: string; startDate: Date; endDate: Date; allDay: boolean; location?: string; notes: string };

/** The event to prefill the system calendar form with, or null if the card has no dated, non-cancelled event.
 *  Default length 1 h; a deadline is 15 minutes ending at the deadline; a date-only event is an all-day entry. */
export function calendarEventFor(card: Card, now: Date = new Date()): CalendarEventDraft | null {
  const e = upcomingEvent(card, now) ?? card.events.find((x) => x.status !== 'cancelled' && parseWall(x.start));
  const w = e ? parseWall(e.start) : null;
  if (!e || !w) return null;
  const notes = [card.links.teams_deep_link, `From Skim · ${card.team.name ?? 'course'}`].filter(Boolean).join('\n\n');
  const base = { title: clip(e.title, 120), location: e.venue ?? undefined, notes };
  if (w.hh === null) return { ...base, allDay: true, startDate: new Date(Date.UTC(w.y, w.m - 1, w.d)), endDate: new Date(Date.UTC(w.y, w.m - 1, w.d + 1)) };
  const s = istInstant(w.y, w.m, w.d, w.hh, w.mm ?? 0);
  if (e.is_deadline) return { ...base, allDay: false, startDate: new Date(s - 15 * 60_000), endDate: new Date(s) };
  const endWall = parseWall(e.end);
  const end = endWall && endWall.hh !== null ? istInstant(endWall.y, endWall.m, endWall.d, endWall.hh, endWall.mm ?? 0) : s + 3_600_000;
  return { ...base, allDay: false, startDate: new Date(s), endDate: new Date(end > s ? end : s + 3_600_000) };
}

/** Android's own "new event" screen as an intent (no permission needed): the fallback if expo-calendar cannot open it. */
export function calendarIntentExtras(d: CalendarEventDraft): Record<string, string | number | boolean> {
  return {
    title: d.title, beginTime: d.startDate.getTime(), endTime: d.endDate.getTime(), allDay: d.allDay,
    ...(d.location ? { eventLocation: d.location } : {}), description: d.notes,
  };
}

// ---------------------------------------------------------------- links
/** Where "Open file" goes: the file card's own link, else the first attachment that has one. */
export function fileUrl(card: Card): string | null {
  return card.links.file_url ?? card.message?.attachments.find((a) => a.web_url)?.web_url ?? null;
}
/** Only web links are ever opened from a card. */
export const safeUrl = (u: string | null | undefined): string | null => (u && /^https:\/\//i.test(u) ? u : null);
