// Display text for cards. The backend sends times with an +05:30 offset (Asia/Kolkata, P4-D-133), so the wall-clock fields are read
// straight from the string instead of going through the phone's time zone or Intl (which differ between devices).
import type { Card, ChangeSide, EventChip } from './types';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WALL = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/;
const IST_OFFSET_MS = 330 * 60_000;

export type Wall = { y: number; m: number; d: number; hh: number | null; mm: number | null };

export function parseWall(iso: string | null | undefined): Wall | null {
  const m = WALL.exec(iso ?? '');
  if (!m) return null;
  return { y: +m[1], m: +m[2], d: +m[3], hh: m[4] === undefined ? null : +m[4], mm: m[5] === undefined ? null : +m[5] };
}

const pad = (n: number) => String(n).padStart(2, '0');
const dow = (w: Wall) => new Date(Date.UTC(w.y, w.m - 1, w.d)).getUTCDay();

/** "Mon 2 Nov" */
export function formatDay(iso: string | null | undefined): string | null {
  const w = parseWall(iso);
  return w ? `${DAYS[dow(w)]} ${w.d} ${MONTHS[w.m - 1]}` : null;
}

/** "10:00", or null for a date-only value */
export function formatTime(iso: string | null | undefined): string | null {
  const w = parseWall(iso);
  return w && w.hh !== null ? `${pad(w.hh)}:${pad(w.mm ?? 0)}` : null;
}

/** "Mon 2 Nov, 10:00" or "Mon 2 Nov" */
export function formatWhen(iso: string | null | undefined): string | null {
  const day = formatDay(iso);
  if (!day) return null;
  const t = formatTime(iso);
  return t ? `${day}, ${t}` : day;
}

/** An event's time line: "Mon 2 Nov, 10:00–11:00" when it has an end on the same day, else start only. */
export function eventWhen(e: Pick<EventChip, 'start' | 'end'>): string | null {
  const start = formatWhen(e.start);
  if (!start) return null;
  const a = parseWall(e.start);
  const b = parseWall(e.end);
  const endTime = formatTime(e.end);
  if (a && b && endTime && a.y === b.y && a.m === b.m && a.d === b.d && a.hh !== null) return `${start}–${endTime}`;
  return start;
}

const ISTDAY = (ms: number) => Math.floor((ms + IST_OFFSET_MS) / 86_400_000);

/** "just now", "5 min ago", "3 h ago", "Yesterday", "4 d ago", then "12 Oct" (and "12 Oct 2025" in an earlier year). */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const diff = now.getTime() - t;
  if (diff < 45_000) return 'just now';
  if (diff < 3_600_000) return `${Math.max(1, Math.round(diff / 60_000))} min ago`;
  if (diff < 86_400_000 && ISTDAY(t) === ISTDAY(now.getTime())) return `${Math.round(diff / 3_600_000)} h ago`;
  const days = ISTDAY(now.getTime()) - ISTDAY(t);
  if (days <= 1) return days === 1 ? 'Yesterday' : `${Math.max(1, Math.round(diff / 3_600_000))} h ago`;
  if (days < 7) return `${days} d ago`;
  const w = parseWall(iso)!;
  const day = `${w.d} ${MONTHS[w.m - 1]}`;
  return w.y === new Date(now.getTime() + IST_OFFSET_MS).getUTCFullYear() ? day : `${day} ${w.y}`;
}

// ---------------------------------------------------------------- change cards
export const CHANGE_LABEL: Record<string, string> = {
  rescheduled: 'Moved', cancelled: 'Cancelled', uncancelled: 'Back on', venue_changed: 'New venue',
  time_set: 'Time set', date_set: 'Date set', details_changed: 'Updated',
};

export type ChangeSummary = { label: string; from: string | null; to: string | null };

const side = (s: ChangeSide, kind: 'when' | 'venue' | 'details'): string | null => {
  if (!s) return null;
  if (kind === 'when') return formatWhen(s.start);
  if (kind === 'venue') return s.venue ?? null;
  return s.details ? clip(s.details, 90) : null;
};

export function changeSummary(change: NonNullable<Card['change']>): ChangeSummary {
  const type = change.type ?? '';
  const label = CHANGE_LABEL[type] ?? 'Updated';
  switch (type) {
    case 'rescheduled':
    case 'time_set':
    case 'date_set':
    case 'uncancelled':
      return { label, from: side(change.before, 'when'), to: side(change.after, 'when') };
    case 'venue_changed':
      return { label, from: side(change.before, 'venue'), to: side(change.after, 'venue') };
    case 'details_changed':
      return { label, from: null, to: side(change.after, 'details') };
    default:
      return { label, from: null, to: null };
  }
}

export function clip(s: string, n: number): string {
  const t = s.replace(/\s+/g, ' ').trim();
  return t.length <= n ? t : `${t.slice(0, n - 1).trimEnd()}…`;
}

// ---------------------------------------------------------------- titles
const DOC_ACTION: Record<string, string> = {
  created: 'New file', modified: 'File updated', renamed: 'File renamed', moved: 'File moved', deleted: 'File deleted', restored: 'File restored',
};
export const docActionLabel = (action: string | null | undefined) => (action ? DOC_ACTION[action] ?? 'File' : 'File');

/** The card's headline. A message shows its subject or its first line; a file shows its name; a change names the event. */
export function cardTitle(c: Card): string {
  if (c.kind === 'message') {
    const m = c.message;
    if (!m) return 'Message';
    if (m.deleted) return 'Deleted in Teams';
    if (m.subject?.trim()) return clip(m.subject, 120);
    const first = m.text.split(/\n/).find((l) => l.trim()) ?? '';
    return first ? clip(first, 90) : c.events[0]?.title ?? 'Post';
  }
  if (c.kind === 'document') return c.document?.name ?? 'File';
  return c.events[0]?.title ?? 'Date change';
}

/** The text under the headline: a message's body (without repeating the headline), a file's action, nothing for a change. */
export function cardBody(c: Card): string {
  if (c.kind === 'message' && c.message && !c.message.deleted) {
    const t = c.message.text.trim();
    if (!t) return '';
    if (c.message.subject?.trim()) return t;
    const lines = t.split(/\n/);
    const i = lines.findIndex((l) => l.trim());
    return lines.slice(i + 1).join('\n').trim(); // the first line is already the headline
  }
  if (c.kind === 'document' && c.document) {
    const d = c.document;
    return d.action === 'renamed' && d.old_name ? `${docActionLabel(d.action)} from “${d.old_name}”` : docActionLabel(d.action);
  }
  return '';
}

/** One line for an event chip: "Quiz 1 · Mon 2 Nov, 10:00 · LHC 104". */
export function eventLine(e: EventChip): string {
  return [e.title, eventWhen(e), e.venue].filter(Boolean).join(' · ');
}

export const EVENT_TYPE_LABEL: Record<string, string> = {
  quiz: 'Quiz', exam: 'Exam', assignment: 'Assignment', project: 'Project', lab: 'Lab', class: 'Class',
  office_hours: 'Office hours', presentation: 'Presentation', other: 'Event',
};

/** The most important marker for the card: the first important event, if any (drives the red highlight). */
export const importantEvent = (c: Card): EventChip | undefined => c.events.find((e) => e.important && e.status !== 'cancelled');
export const hasUnconfirmed = (c: Card): boolean => c.events.some((e) => e.unconfirmed);

// ---------------------------------------------------------------- starred
/** The expiry line of a temporary star: "2 days left", "5 h left", "until Mon 9 Nov" (a week or more away). null for a permanent star.
 *  A star that has already run out gives "expired" (the server drops those, but the phone's clock may be ahead of the last refresh). */
export function starExpiryLabel(expiresAt: string | null | undefined, now: Date = new Date()): string | null {
  if (!expiresAt) return null;
  const diff = new Date(expiresAt).getTime() - now.getTime();
  if (Number.isNaN(diff)) return null;
  if (diff <= 0) return 'expired';
  if (diff < 3_600_000) return 'less than an hour left';
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} h left`;
  const days = Math.ceil(diff / 86_400_000);
  if (days <= 6) return `${days} ${days === 1 ? 'day' : 'days'} left`;
  const day = formatDay(expiresAt);
  return day ? `until ${day}` : null;
}
