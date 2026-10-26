import type { KeyValue } from '@/lib/outbox';
import {
  backgroundSync, EMPTY_STATE, HORIZON_DAYS, isSkimNotification, MAX_LOCAL, planSchedule, readState, reconcile, routeForNotification, STATE_KEY, syncReminders, writeState,
  type Planned, type RemindersResponse, type SchedulerLike, type ServerReminder, type SyncResponse,
} from '@/lib/reminder-plan';

const NOW = new Date('2026-10-26T12:00:00+05:30');
const at = (offsetMin: number) => new Date(NOW.getTime() + offsetMin * 60_000).toISOString();
const rem = (id: string, minutes: number, over: Partial<ServerReminder> = {}): ServerReminder => ({
  id, kind: 'manual', item_id: 7, event_id: 1, fire_at: at(minutes), title: 'Quiz 1', body: 'Tomorrow: Mon 2 Nov, 10:00', ...over,
});

describe('planSchedule', () => {
  test('keeps future reminders, soonest first, with the data a tap needs', () => {
    const p = planSchedule([rem('b', 600), rem('a', 60)], NOW);
    expect(p.map((x) => x.id)).toEqual(['a', 'b']);
    expect(p[0]).toMatchObject({ title: 'Quiz 1', body: 'Tomorrow: Mon 2 Nov, 10:00', data: { type: 'reminder', reminder_id: 'a', item_id: 7 } });
    expect(p[0].at.getTime()).toBe(NOW.getTime() + 3_600_000);
  });
  test('drops the past, "right now", and anything beyond 30 days', () => {
    const p = planSchedule([rem('past', -5), rem('now', 0), rem('soon', 0.05), rem('ok', 1), rem('edge', HORIZON_DAYS * 1440), rem('far', HORIZON_DAYS * 1440 + 1)], NOW);
    expect(p.map((x) => x.id)).toEqual(['ok', 'edge']);
  });
  test('at most 60, soonest first, and the same id twice counts once', () => {
    const many = Array.from({ length: 80 }, (_, i) => rem(`r${String(i).padStart(2, '0')}`, 1000 - i));
    const p = planSchedule([...many, rem('r00', 5)], NOW);
    expect(p).toHaveLength(MAX_LOCAL);
    expect(p.map((x) => x.at.getTime())).toEqual([...p.map((x) => x.at.getTime())].sort((a, b) => a - b));
    expect(new Set(p.map((x) => x.id)).size).toBe(MAX_LOCAL);
    expect(p[0].id).toBe('r79'); // the soonest of the first occurrences; a later duplicate id is ignored
  });
  test('ties are ordered by id so the plan is stable', () => {
    expect(planSchedule([rem('b', 60), rem('a', 60)], NOW).map((x) => x.id)).toEqual(['a', 'b']);
  });
  test('garbage in the response cannot crash it or schedule nonsense', () => {
    const bad = [
      rem('x1', 60, { fire_at: 'not a date' }), rem('x2', 60, { fire_at: '' }), { ...rem('x3', 60), id: '' }, null as unknown as ServerReminder,
      rem('x4', 60, { item_id: -3 }), rem('x5', 60, { item_id: 1.5 }), rem('x6', 60, { item_id: null }), rem('x7', 60, { title: '  ', body: 5 as unknown as string }),
    ];
    const p = planSchedule(bad, NOW);
    expect(p.map((x) => x.id)).toEqual(['x4', 'x5', 'x6', 'x7']);
    for (const x of p.slice(0, 3)) expect(x.data).not.toHaveProperty('item_id');
    expect(p[3]).toMatchObject({ title: 'Skim reminder', body: '' });
  });
  test('long text is cut to what a notification can show, whitespace tidied', () => {
    const p = planSchedule([rem('a', 60, { title: 'T '.repeat(100), body: 'line one\n\nline   two ' + 'x'.repeat(300) })], NOW)[0];
    expect(p.title.length).toBeLessThanOrEqual(80);
    expect(p.body.length).toBeLessThanOrEqual(160);
    expect(p.body.startsWith('line one line two')).toBe(true);
  });
  test('does not modify what it is given', () => {
    const input = [rem('a', 60)];
    const copy = JSON.parse(JSON.stringify(input));
    planSchedule(input, NOW);
    expect(input).toEqual(copy);
  });
});

test('reconcile cancels only what the server dropped and reschedules the rest under the same identifier', () => {
  const planned = planSchedule([rem('keep', 60), rem('new', 120)], NOW);
  const r = reconcile(['keep', 'gone', 'also-gone'], planned);
  expect(r.cancel).toEqual(['gone', 'also-gone']);
  expect(r.schedule.map((p) => p.id)).toEqual(['keep', 'new']);
  expect(reconcile([], [])).toEqual({ cancel: [], schedule: [] });
});

// ---------------------------------------------------------------- syncReminders
function scheduler(over: Partial<SchedulerLike & { existing: string[] }> = {}) {
  const log: string[] = [];
  const s: SchedulerLike & { log: string[]; scheduled: Planned[] } = {
    log, scheduled: [],
    permissionGranted: over.permissionGranted ?? (async () => true),
    scheduledIds: over.scheduledIds ?? (async () => over.existing ?? []),
    cancel: over.cancel ?? (async (id) => void log.push(`cancel:${id}`)),
    schedule: over.schedule ?? (async (p) => { log.push(`schedule:${p.id}`); s.scheduled.push(p); }),
  };
  return s;
}
const response = (...r: ServerReminder[]): RemindersResponse => ({ reminders: r, etag: 'e1' });

describe('syncReminders', () => {
  test('cancels stale ones, schedules the wanted ones, reports the etag', async () => {
    const s = scheduler({ existing: ['old'] });
    const r = await syncReminders({ fetchReminders: async () => response(rem('a', 60), rem('b', 90)), scheduler: s, now: () => NOW });
    expect(r).toEqual({ status: 'scheduled', scheduled: 2, cancelled: 1, etag: 'e1' });
    expect(s.log).toEqual(['cancel:old', 'schedule:a', 'schedule:b']);
  });
  test('without notification permission nothing is fetched or scheduled', async () => {
    const fetchReminders = jest.fn(async () => response());
    const s = scheduler({ permissionGranted: async () => false });
    expect(await syncReminders({ fetchReminders, scheduler: s, now: () => NOW })).toEqual({ status: 'no_permission' });
    expect(fetchReminders).not.toHaveBeenCalled();
    expect(s.log).toEqual([]);
  });
  test('a permission check that throws counts as "no permission", not a crash', async () => {
    const s = scheduler({ permissionGranted: async () => { throw new Error('x'); } });
    expect(await syncReminders({ fetchReminders: async () => response(), scheduler: s })).toEqual({ status: 'no_permission' });
  });
  test('a failed or malformed fetch leaves the phone’s existing reminders alone', async () => {
    const s = scheduler({ existing: ['keep-me'] });
    expect(await syncReminders({ fetchReminders: async () => { throw new Error('offline'); }, scheduler: s })).toEqual({ status: 'failed', stage: 'fetch' });
    expect(await syncReminders({ fetchReminders: async () => ({ nope: 1 }) as unknown as RemindersResponse, scheduler: s })).toEqual({ status: 'failed', stage: 'fetch' });
    expect(s.log).toEqual([]); // nothing cancelled: an outage must not wipe the reminders
  });
  test('a scheduler error is reported, not thrown', async () => {
    const s = scheduler({ schedule: async () => { throw new Error('alarm'); } });
    expect(await syncReminders({ fetchReminders: async () => response(rem('a', 60)), scheduler: s, now: () => NOW })).toEqual({ status: 'failed', stage: 'schedule' });
  });
  test('an empty list cancels everything', async () => {
    const s = scheduler({ existing: ['a', 'b'] });
    expect(await syncReminders({ fetchReminders: async () => response(), scheduler: s, now: () => NOW })).toMatchObject({ status: 'scheduled', scheduled: 0, cancelled: 2 });
  });
});

// ---------------------------------------------------------------- backgroundSync
function memory(): KeyValue & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: async (k) => data.get(k) ?? null, setItem: async (k, v) => void data.set(k, v), removeItem: async (k) => void data.delete(k) };
}
const sync = (over: Partial<SyncResponse> = {}): SyncResponse => ({ deck_changed: false, reminders_etag: 'e1', server_time: '2026-10-26T12:00:00+05:30', ...over });

describe('backgroundSync', () => {
  const run = (store: KeyValue, fetchSync: (s: string | null) => Promise<SyncResponse>, reschedule: jest.Mock = jest.fn(async () => ({ status: 'scheduled', scheduled: 1, cancelled: 0, etag: 'e1' }) as const)) =>
    backgroundSync({ userId: async () => 'u1', store, fetchSync, syncReminders: reschedule });

  test('no session: nothing happens', async () => {
    const fetchSync = jest.fn();
    expect(await backgroundSync({ userId: async () => null, store: memory(), fetchSync, syncReminders: jest.fn() })).toBe('no_session');
    expect(fetchSync).not.toHaveBeenCalled();
  });
  test('the first run reschedules (the etag is new) and remembers the etag and server time', async () => {
    const store = memory();
    expect(await run(store, async () => sync())).toBe('rescheduled');
    expect(await readState(store, 'u1')).toEqual({ since: '2026-10-26T12:00:00+05:30', etag: 'e1', deckStale: false });
  });
  test('an unchanged etag does not refetch or reschedule anything', async () => {
    const store = memory();
    await writeState(store, 'u1', { since: 'old', etag: 'e1', deckStale: false });
    const reschedule = jest.fn();
    expect(await run(store, async () => sync(), reschedule as never)).toBe('unchanged');
    expect(reschedule).not.toHaveBeenCalled();
    expect((await readState(store, 'u1')).since).toBe('2026-10-26T12:00:00+05:30'); // the clock still moves on
  });
  test('it asks /sync from where it left off', async () => {
    const store = memory();
    await writeState(store, 'u1', { since: '2026-10-26T09:00:00+05:30', etag: 'e1', deckStale: false });
    const seen: (string | null)[] = [];
    await run(store, async (s) => { seen.push(s); return sync(); });
    expect(seen).toEqual(['2026-10-26T09:00:00+05:30']);
  });
  test('a changed etag reschedules and stores the new one', async () => {
    const store = memory();
    await writeState(store, 'u1', { since: null, etag: 'old', deckStale: false });
    const reschedule = jest.fn(async () => ({ status: 'scheduled', scheduled: 3, cancelled: 1, etag: 'e2' }) as const);
    expect(await run(store, async () => sync({ reminders_etag: 'e2' }), reschedule)).toBe('rescheduled');
    expect((await readState(store, 'u1')).etag).toBe('e2');
  });
  test('a new card marks the deck stale, and stays marked until the app clears it', async () => {
    const store = memory();
    await writeState(store, 'u1', { since: null, etag: 'e1', deckStale: false });
    await run(store, async () => sync({ deck_changed: true }));
    expect((await readState(store, 'u1')).deckStale).toBe(true);
    await run(store, async () => sync({ deck_changed: false }));
    expect((await readState(store, 'u1')).deckStale).toBe(true);
  });
  test('/sync failing changes nothing', async () => {
    const store = memory();
    await writeState(store, 'u1', { since: 'x', etag: 'e0', deckStale: false });
    expect(await run(store, async () => { throw new Error('offline'); })).toBe('failed');
    expect(await readState(store, 'u1')).toEqual({ since: 'x', etag: 'e0', deckStale: false });
  });
  test('a failed reschedule keeps the old etag so the next run tries again', async () => {
    const store = memory();
    await writeState(store, 'u1', { since: 'x', etag: 'e0', deckStale: false });
    expect(await run(store, async () => sync({ reminders_etag: 'e2' }), jest.fn(async () => ({ status: 'failed', stage: 'fetch' }) as const))).toBe('failed');
    expect((await readState(store, 'u1')).etag).toBe('e0');
  });
  test('without notification permission the etag is not recorded, so granting it later still schedules', async () => {
    const store = memory();
    expect(await run(store, async () => sync(), jest.fn(async () => ({ status: 'no_permission' }) as const))).toBe('unchanged');
    expect((await readState(store, 'u1')).etag).toBeNull();
  });
  test('state is per account and a corrupt state file reads as empty', async () => {
    const store = memory();
    await writeState(store, 'u1', { since: 's', etag: 'e', deckStale: true });
    expect(await readState(store, 'u2')).toEqual(EMPTY_STATE);
    store.data.set(STATE_KEY('u3'), '{nope');
    expect(await readState(store, 'u3')).toEqual(EMPTY_STATE);
    store.data.set(STATE_KEY('u4'), JSON.stringify({ since: 5, etag: {}, deckStale: 'yes' }));
    expect(await readState(store, 'u4')).toEqual(EMPTY_STATE);
  });
});

describe('tapping a notification', () => {
  test.each([
    [{ type: 'reminder', item_id: 7 }, '7'], [{ type: 'sync', event_id: 1, item_id: 42 }, '42'], [{ item_id: '15' }, '15'],
  ])('%j opens card %s', (data, id) => expect(routeForNotification(data)).toEqual({ pathname: '/item/[id]', params: { id } }));

  test.each([
    [{ item_id: 0 }], [{ item_id: -4 }], [{ item_id: 1.5 }], [{ item_id: 'abc' }], [{ item_id: '12abc' }], [{ item_id: '../../etc' }], [{ item_id: '7/state' }],
    [{ item_id: '1'.repeat(30) }], [{ item_id: NaN }], [{ item_id: null }], [{}], [null], [undefined], ['7'], [7],
  ])('%j opens the deck instead', (data) => expect(routeForNotification(data)).toBeNull());

  test('only this app’s own notifications are recognised', () => {
    expect(isSkimNotification({ type: 'reminder' })).toBe(true);
    expect(isSkimNotification({ type: 'sync' })).toBe(true);
    for (const d of [{ type: 'other' }, {}, null, undefined, 'reminder', 5]) expect(isSkimNotification(d)).toBe(false);
  });
});
