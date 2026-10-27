// The student's settings (PHASE4.md section 5), read from /me and changed with PATCH /me/settings. The ranges here mirror the server's
// validator (skim/api/user_settings.py); the server stays the judge, this keeps the controls inside what it will accept.
import { deepMerge } from './outbox';

export const EVENT_TYPES = ['quiz', 'exam', 'assignment', 'project', 'lab', 'class', 'office_hours', 'presentation', 'other'] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export type Settings = {
  timezone: string;
  history_limit: number;
  quiet_hours: { enabled: boolean; start: string; end: string };
  urgent_only: boolean;
  new_card_alerts: boolean; // a push when a new post or file arrives in a course (mute and quiet hours still apply)
  important_types: string[];
  reminders: { day_before_time: string; same_day_hours: number; earliest: string; auto_for_important: boolean };
};
export type SettingsPatch = { [K in keyof Settings]?: Settings[K] extends object ? Partial<Settings[K]> : Settings[K] };

export const DEFAULTS: Settings = {
  timezone: 'Asia/Kolkata',
  history_limit: 50,
  quiet_hours: { enabled: true, start: '23:00', end: '07:00' },
  urgent_only: false,
  new_card_alerts: true,
  important_types: ['quiz', 'exam', 'assignment'],
  reminders: { day_before_time: '20:00', same_day_hours: 5, earliest: '07:00', auto_for_important: true },
};

export const HISTORY_RANGE = { min: 10, max: 500 } as const;
export const HISTORY_CHOICES = [10, 25, 50, 100, 200, 300, 500] as const;
export const HOURS_RANGE = { min: 0.5, max: 24 } as const;

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const bool = (v: unknown, d: boolean) => (typeof v === 'boolean' ? v : d);
const hhmm = (v: unknown, d: string) => (typeof v === 'string' && HHMM.test(v) ? v : d);

/** Whatever /me sent -> a complete, valid Settings. A missing or odd field falls back to its default instead of breaking the screen. */
export function parseSettings(raw: unknown): Settings {
  const r = isObj(raw) ? raw : {};
  const q = isObj(r.quiet_hours) ? r.quiet_hours : {};
  const m = isObj(r.reminders) ? r.reminders : {};
  const hist = typeof r.history_limit === 'number' && Number.isInteger(r.history_limit) ? Math.min(HISTORY_RANGE.max, Math.max(HISTORY_RANGE.min, r.history_limit)) : DEFAULTS.history_limit;
  const hours = typeof m.same_day_hours === 'number' && m.same_day_hours >= HOURS_RANGE.min && m.same_day_hours <= HOURS_RANGE.max && (m.same_day_hours * 2) % 1 === 0
    ? m.same_day_hours : DEFAULTS.reminders.same_day_hours;
  const types = Array.isArray(r.important_types) ? EVENT_TYPES.filter((t) => (r.important_types as unknown[]).includes(t)) : DEFAULTS.important_types;
  return {
    timezone: DEFAULTS.timezone,
    history_limit: hist,
    quiet_hours: { enabled: bool(q.enabled, DEFAULTS.quiet_hours.enabled), start: hhmm(q.start, DEFAULTS.quiet_hours.start), end: hhmm(q.end, DEFAULTS.quiet_hours.end) },
    urgent_only: bool(r.urgent_only, DEFAULTS.urgent_only),
    new_card_alerts: bool(r.new_card_alerts, DEFAULTS.new_card_alerts),
    important_types: [...types],
    reminders: {
      day_before_time: hhmm(m.day_before_time, DEFAULTS.reminders.day_before_time), same_day_hours: hours,
      earliest: hhmm(m.earliest, DEFAULTS.reminders.earliest), auto_for_important: bool(m.auto_for_important, DEFAULTS.reminders.auto_for_important),
    },
  };
}

/** Apply a patch the way the server will, for the instant (optimistic) update. */
export const applyPatch = (current: Settings, patch: SettingsPatch): Settings => parseSettings(deepMerge(current, patch));

/** Tick or untick a type. The list always comes back in the canonical order, without duplicates or unknown types. */
export function toggleType(types: readonly string[], type: string): string[] {
  if (!(EVENT_TYPES as readonly string[]).includes(type)) return EVENT_TYPES.filter((t) => types.includes(t));
  const on = types.includes(type);
  return EVENT_TYPES.filter((t) => (t === type ? !on : types.includes(t)));
}

// ---------------------------------------------------------------- steppers
export const toMinutes = (hhmmText: string): number => Number(hhmmText.slice(0, 2)) * 60 + Number(hhmmText.slice(3, 5));
export function fromMinutes(total: number): string {
  const m = ((Math.round(total) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}
/** Move a time of day by `delta` minutes (wraps around midnight). */
export const stepTime = (value: string, delta: number): string => fromMinutes(toMinutes(value) + delta);

export function stepHours(hours: number, dir: 1 | -1): number {
  const next = Math.round((hours + dir * 0.5) * 2) / 2;
  return Math.min(HOURS_RANGE.max, Math.max(HOURS_RANGE.min, next));
}
export const formatHours = (h: number): string => (h < 1 ? `${Math.round(h * 60)} minutes` : h === 1 ? '1 hour' : `${h} hours`);

/** Step through the sensible history sizes (10, 25, 50, 100, 200, 300, 500). */
export function stepHistory(n: number, dir: 1 | -1): number {
  const list = HISTORY_CHOICES as readonly number[];
  if (dir === 1) return list.find((c) => c > n) ?? HISTORY_RANGE.max;
  return [...list].reverse().find((c) => c < n) ?? HISTORY_RANGE.min;
}

/** "23:00 to 07:00": the window, said the way a student would. */
export const quietWindow = (q: Settings['quiet_hours']): string => `${q.start} to ${q.end}`;
