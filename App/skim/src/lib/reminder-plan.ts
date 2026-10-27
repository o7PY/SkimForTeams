// Which of the server's reminders become local notifications on this phone (PHASE4.md P4-D-120, 7.3). The server decides WHEN each one
// fires (skim/reminders.py); the phone only schedules what it is told: the next 30 days, at most 60, soonest first.
import type { KeyValue } from './outbox';

export type ServerReminder = {
  id: string;
  kind: string;
  item_id: number | null;
  event_id: number | null;
  fire_at: string;
  title: string;
  body: string;
  virtual?: boolean;
};
/** A row of the Reminders list: a reminder that is on (scheduled on this phone) or switched off (kept, not scheduled). */
export type ListedReminder = ServerReminder & { enabled: boolean };
export type RemindersAllResponse = { reminders: ListedReminder[]; computed_at?: string };
export type RemindersResponse = { reminders: ServerReminder[]; etag: string; computed_at?: string };

export type Planned = { id: string; at: Date; title: string; body: string; data: { type: 'reminder'; reminder_id: string; item_id?: number } };

export const MAX_LOCAL = 60;
export const HORIZON_DAYS = 30;
const LEAD_MS = 5_000; // a time this close to now is already too late to schedule
const clean = (s: unknown, fallback: string, max: number) => {
  const t = typeof s === 'string' ? s.replace(/\s+/g, ' ').trim() : '';
  return (t || fallback).slice(0, max);
};

export function planSchedule(reminders: readonly ServerReminder[], now: Date): Planned[] {
  const horizon = now.getTime() + HORIZON_DAYS * 86_400_000;
  const seen = new Set<string>();
  const out: Planned[] = [];
  for (const r of reminders) {
    if (!r || typeof r.id !== 'string' || !r.id || seen.has(r.id)) continue;
    const t = new Date(r.fire_at).getTime();
    if (Number.isNaN(t) || t <= now.getTime() + LEAD_MS || t > horizon) continue;
    seen.add(r.id);
    const item = Number.isInteger(r.item_id) && (r.item_id as number) > 0 ? { item_id: r.item_id as number } : {};
    out.push({ id: r.id, at: new Date(t), title: clean(r.title, 'Skim reminder', 80), body: clean(r.body, '', 160), data: { type: 'reminder', reminder_id: r.id, ...item } });
  }
  out.sort((a, b) => a.at.getTime() - b.at.getTime() || (a.id < b.id ? -1 : 1));
  return out.slice(0, MAX_LOCAL);
}

/** What to do to the phone's scheduled list: cancel what the server no longer wants; (re)schedule everything it does, using the
 *  reminder's id as the notification's identifier so a moved time REPLACES the old alarm instead of adding a second one. */
export function reconcile(existingIds: readonly string[], planned: readonly Planned[]): { cancel: string[]; schedule: Planned[] } {
  const want = new Set(planned.map((p) => p.id));
  return { cancel: existingIds.filter((id) => !want.has(id)), schedule: [...planned] };
}

// ---------------------------------------------------------------- applying it
export type SchedulerLike = {
  permissionGranted(): Promise<boolean>;
  scheduledIds(): Promise<string[]>;
  cancel(id: string): Promise<void>;
  schedule(p: Planned): Promise<void>;
};

export type SyncRemindersResult =
  | { status: 'scheduled'; scheduled: number; cancelled: number; etag: string }
  | { status: 'no_permission' }
  | { status: 'failed'; stage: 'fetch' | 'schedule' };

export const STATE_KEY = (userId: string) => `skim-notif-state:${userId}`;
export type SyncState = { since: string | null; etag: string | null; deckStale: boolean };
export const EMPTY_STATE: SyncState = { since: null, etag: null, deckStale: false };

export async function readState(store: KeyValue, userId: string): Promise<SyncState> {
  try {
    const v = JSON.parse((await store.getItem(STATE_KEY(userId))) ?? 'null');
    return v && typeof v === 'object' ? { since: typeof v.since === 'string' ? v.since : null, etag: typeof v.etag === 'string' ? v.etag : null, deckStale: v.deckStale === true } : EMPTY_STATE;
  } catch {
    return EMPTY_STATE;
  }
}
export const writeState = (store: KeyValue, userId: string, s: SyncState) => store.setItem(STATE_KEY(userId), JSON.stringify(s)).catch(() => undefined);

/** Fetch the server's reminders and make the phone's scheduled notifications match them. Never throws. */
export async function syncReminders(deps: { fetchReminders(): Promise<RemindersResponse>; scheduler: SchedulerLike; now?: () => Date }): Promise<SyncRemindersResult> {
  const now = (deps.now ?? (() => new Date()))();
  if (!(await deps.scheduler.permissionGranted().catch(() => false))) return { status: 'no_permission' };
  let res: RemindersResponse;
  try {
    res = await deps.fetchReminders();
    if (!res || !Array.isArray(res.reminders)) throw new Error('bad response');
  } catch {
    return { status: 'failed', stage: 'fetch' };
  }
  try {
    const { cancel, schedule } = reconcile(await deps.scheduler.scheduledIds(), planSchedule(res.reminders, now));
    for (const id of cancel) await deps.scheduler.cancel(id);
    for (const p of schedule) await deps.scheduler.schedule(p);
    return { status: 'scheduled', scheduled: schedule.length, cancelled: cancel.length, etag: res.etag };
  } catch {
    return { status: 'failed', stage: 'schedule' };
  }
}

// ---------------------------------------------------------------- background sync (P4-D-132)
export type SyncResponse = { deck_changed: boolean; reminders_etag: string; server_time: string };
export type BackgroundResult = 'no_session' | 'unchanged' | 'rescheduled' | 'failed';

/** What the background task does: one cheap /sync call; only if the reminders changed, fetch and reschedule; remember if the deck
 *  changed so the app refreshes it when opened. Pure apart from the injected functions. */
export async function backgroundSync(deps: {
  userId(): Promise<string | null>;
  store: KeyValue;
  fetchSync(since: string | null): Promise<SyncResponse>;
  syncReminders(): Promise<SyncRemindersResult>;
}): Promise<BackgroundResult> {
  const userId = await deps.userId();
  if (!userId) return 'no_session';
  const state = await readState(deps.store, userId);
  let s: SyncResponse;
  try {
    s = await deps.fetchSync(state.since);
  } catch {
    return 'failed';
  }
  let etag = state.etag;
  let result: BackgroundResult = 'unchanged';
  if (s.reminders_etag !== state.etag) {
    const r = await deps.syncReminders();
    if (r.status === 'failed') return 'failed'; // leave the saved etag alone so the next run tries again
    if (r.status === 'scheduled') { etag = r.etag; result = 'rescheduled'; }
  }
  await writeState(deps.store, userId, { since: s.server_time, etag, deckStale: state.deckStale || s.deck_changed });
  return result;
}

// ---------------------------------------------------------------- notification taps
export type Target = { pathname: '/item/[id]'; params: { id: string } } | null;

/** Where a tapped notification goes. Only a positive whole item id is trusted; anything else opens the deck (null). */
export function routeForNotification(data: unknown): Target {
  const d = data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
  const raw = d.item_id;
  const n = typeof raw === 'number' ? raw : typeof raw === 'string' && /^\d{1,15}$/.test(raw) ? Number(raw) : NaN;
  return Number.isSafeInteger(n) && n > 0 ? { pathname: '/item/[id]', params: { id: String(n) } } : null;
}

/** Notifications this app sent (reminders, or pushes carrying `type: sync`); anything else is ignored. */
export const isSkimNotification = (data: unknown): boolean => {
  const t = data && typeof data === 'object' ? (data as Record<string, unknown>).type : undefined;
  return t === 'reminder' || t === 'sync';
};
