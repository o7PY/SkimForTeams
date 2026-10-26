import { ApiError } from '@/lib/api';
import { deepMerge, MAX_ATTEMPTS, Outbox, storageKey, type KeyValue, type OutboxOp } from '@/lib/outbox';

function memory(): KeyValue & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: async (k) => data.get(k) ?? null, setItem: async (k, v) => void data.set(k, v), removeItem: async (k) => void data.delete(k) };
}

type Script = (op: OutboxOp) => void | Promise<void>; // throws to fail
function make(store = memory(), script: Script = () => undefined, opts: ConstructorParameters<typeof Outbox>[2] = {}) {
  const sent: OutboxOp[] = [];
  let n = 0;
  const box = new Outbox(store, async (op) => { await script(op); sent.push(op); }, { uuid: () => `id${++n}`, now: () => 1000, ...opts });
  return { box, sent, store };
}

const dismiss = (id: number) => ({ key: `state:${id}`, method: 'POST' as const, path: `/items/${id}/state`, body: { action: 'dismiss' } });
const err = (status: number, body: { error?: string } | null = null) => new ApiError(status, body);

test('operations are replayed in the order they were made, then the queue is empty and persisted as empty', async () => {
  const { box, sent, store } = make();
  await box.load('u1');
  await box.enqueue(dismiss(1));
  await box.enqueue({ key: 'star:2', method: 'PUT', path: '/items/2/star', body: { temp: null } });
  await box.enqueue(dismiss(3));
  expect(box.size).toBe(3);
  expect(await box.flush()).toMatchObject({ status: 'done', sent: 3, rejected: [] });
  expect(sent.map((o) => o.path)).toEqual(['/items/1/state', '/items/2/star', '/items/3/state']);
  expect(box.size).toBe(0);
  expect(JSON.parse(store.data.get(storageKey('u1'))!).ops).toEqual([]);
});

test('last write wins: a newer operation on the same thing replaces the older one (dismiss then undo = undo only)', async () => {
  const { box, sent } = make();
  await box.load('u1');
  await box.enqueue(dismiss(1));
  await box.enqueue({ key: 'star:1', method: 'PUT', path: '/items/1/star', body: { temp: '1d' } });
  await box.enqueue({ key: 'state:1', method: 'POST', path: '/items/1/state', body: { action: 'undo' } });
  await box.enqueue({ key: 'star:1', method: 'DELETE', path: '/items/1/star' });
  expect(box.snapshot().map((o) => [o.method, o.path])).toEqual([['POST', '/items/1/state'], ['DELETE', '/items/1/star']]);
  await box.flush();
  expect(sent.map((o) => o.body)).toEqual([{ action: 'undo' }, undefined]);
});

test('settings patches MERGE instead of replacing, so two quick edits both survive', async () => {
  const { box } = make();
  await box.load('u1');
  const patch = (body: object) => ({ key: 'settings', method: 'PATCH' as const, path: '/me/settings', body, merge: true });
  await box.enqueue(patch({ history_limit: 80, quiet_hours: { start: '22:30' } }));
  await box.enqueue(patch({ quiet_hours: { end: '06:30' }, urgent_only: true }));
  await box.enqueue(patch({ history_limit: 120, important_types: ['quiz'] }));
  expect(box.size).toBe(1);
  expect(box.snapshot()[0].body).toEqual({ history_limit: 120, quiet_hours: { start: '22:30', end: '06:30' }, urgent_only: true, important_types: ['quiz'] });
});

test('deepMerge: objects merge, arrays and scalars are replaced, inputs are not mutated', () => {
  const a = { x: { y: 1, z: [1, 2] }, k: 1 };
  expect(deepMerge(a, { x: { z: [9] }, k: null })).toEqual({ x: { y: 1, z: [9] }, k: null });
  expect(a).toEqual({ x: { y: 1, z: [1, 2] }, k: 1 });
  expect(deepMerge(5, { a: 1 })).toEqual({ a: 1 });
});

test('offline stops the flush at the first failure and keeps everything, in order', async () => {
  let online = false;
  const { box, sent } = make(memory(), () => { if (!online) throw err(0); });
  await box.load('u1');
  await box.enqueue(dismiss(1));
  await box.enqueue(dismiss(2));
  expect(await box.flush()).toMatchObject({ status: 'offline', sent: 0 });
  expect(box.size).toBe(2);
  expect(box.snapshot()[0].attempts).toBe(0); // being offline is not the operation's fault
  online = true;
  expect(await box.flush()).toMatchObject({ status: 'done', sent: 2 });
  expect(sent.map((o) => o.path)).toEqual(['/items/1/state', '/items/2/state']);
});

test('a 4xx drops that one operation (the item is gone) and delivery continues; the drop is reported', async () => {
  const { box, sent } = make(memory(), (op) => { if (op.path.includes('/2/')) throw err(404, { error: 'not found' }); });
  await box.load('u1');
  for (const i of [1, 2, 3]) await box.enqueue(dismiss(i));
  const r = await box.flush();
  expect(r.status).toBe('done');
  expect(r.sent).toBe(2);
  expect(r.rejected).toHaveLength(1);
  expect(r.rejected[0]).toMatchObject({ status: 404, reason: 'not found' });
  expect(r.rejected[0].op.path).toBe('/items/2/state');
  expect(sent.map((o) => o.path)).toEqual(['/items/1/state', '/items/3/state']);
  expect(box.dropped).toHaveLength(1);
});

test('a 5xx or 429 stops the flush and retries later, but a poison operation is dropped after MAX_ATTEMPTS so it cannot block the queue forever', async () => {
  const { box, sent } = make(memory(), (op) => { if (op.path.includes('/1/')) throw err(500); });
  await box.load('u1');
  await box.enqueue(dismiss(1));
  await box.enqueue(dismiss(2));
  for (let i = 1; i < MAX_ATTEMPTS; i++) {
    expect(await box.flush()).toMatchObject({ status: 'retry', sent: 0 });
    expect(box.snapshot()[0].attempts).toBe(i);
  }
  const last = await box.flush();
  expect(last.status).toBe('done');
  expect(last.rejected[0].op.path).toBe('/items/1/state');
  expect(sent.map((o) => o.path)).toEqual(['/items/2/state']); // the one behind it went through
});

test('an unexpected exception is treated like a server hiccup, not a crash', async () => {
  const { box } = make(memory(), () => { throw new TypeError('boom'); });
  await box.load('u1');
  await box.enqueue(dismiss(1));
  expect(await box.flush()).toMatchObject({ status: 'retry' });
  expect(box.size).toBe(1);
});

test('a 401 stops everything without dropping anything (the sign-in has to be fixed first)', async () => {
  const { box } = make(memory(), () => { throw err(401); });
  await box.load('u1');
  await box.enqueue(dismiss(1));
  expect(await box.flush()).toMatchObject({ status: 'unauthorized' });
  expect(box.size).toBe(1);
});

test('the queue survives an app restart (a new Outbox on the same storage picks it up)', async () => {
  const store = memory();
  const a = make(store);
  await a.box.load('u1');
  await a.box.enqueue(dismiss(1));
  await a.box.enqueue({ key: 'star:1', method: 'PUT', path: '/items/1/star', body: { temp: '3d' } });
  const b = make(store);
  await b.box.load('u1');
  expect(b.box.snapshot().map((o) => o.path)).toEqual(['/items/1/state', '/items/1/star']);
  await b.box.flush();
  expect(b.sent).toHaveLength(2);
});

test('one account never replays another account’s operations', async () => {
  const store = memory();
  const a = make(store);
  await a.box.load('u1');
  await a.box.enqueue(dismiss(1));
  const b = make(store);
  await b.box.load('u2');
  expect(b.box.size).toBe(0);
  // and a file whose recorded owner does not match its key is ignored
  store.data.set(storageKey('u3'), store.data.get(storageKey('u1'))!);
  const c = make(store);
  await c.box.load('u3');
  expect(c.box.size).toBe(0);
});

test('a corrupt or hostile saved queue is ignored, and bad entries are filtered out', async () => {
  const store = memory();
  store.data.set(storageKey('u1'), '{not json');
  const a = make(store);
  await a.box.load('u1');
  expect(a.box.size).toBe(0);
  store.data.set(storageKey('u1'), JSON.stringify({ userId: 'u1', ops: [
    { id: 'a', key: 'k', method: 'POST', path: '/items/1/state', attempts: 0, createdAt: 1 },
    { id: 'b', key: 'k2', method: 'GET', path: '/items/1', attempts: 0, createdAt: 1 },        // GET is not a mutation
    { id: 'c', key: 'k3', method: 'POST', path: 'https://evil.example/x', attempts: 0, createdAt: 1 }, // not an API path
    { id: 'd' }, null, 5,
  ] }));
  const b = make(store);
  await b.box.load('u1');
  expect(b.box.snapshot().map((o) => o.id)).toEqual(['a']);
});

test('an operation enqueued while another is being sent is kept; the one in flight is not replaced', async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  let first = true;
  const { box, sent } = make(memory(), async () => { if (first) { first = false; await gate; } });
  await box.load('u1');
  await box.enqueue(dismiss(1));
  const flushing = box.flush();
  await Promise.resolve();
  await box.enqueue({ key: 'state:1', method: 'POST', path: '/items/1/state', body: { action: 'undo' } }); // same key, mid-flight
  expect(box.size).toBe(2);
  release();
  await flushing;
  expect(sent.map((o) => (o.body as { action: string }).action)).toEqual(['dismiss', 'undo']);
});

test('two flushes at once do not send anything twice', async () => {
  const { box, sent } = make();
  await box.load('u1');
  await box.enqueue(dismiss(1));
  const [a, b] = await Promise.all([box.flush(), box.flush()]);
  expect([a.status, b.status].sort()).toEqual(['busy', 'done']);
  expect(sent).toHaveLength(1);
});

test('a full queue drops the oldest operation and says so', async () => {
  const { box } = make(memory(), () => undefined, { maxOps: 3 });
  await box.load('u1');
  for (const i of [1, 2, 3, 4]) await box.enqueue(dismiss(i));
  expect(box.snapshot().map((o) => o.path)).toEqual(['/items/2/state', '/items/3/state', '/items/4/state']);
  expect(box.dropped).toEqual([expect.objectContaining({ reason: 'queue_full' })]);
});

test('clear forgets the queue and removes it from storage', async () => {
  const { box, store } = make();
  await box.load('u1');
  await box.enqueue(dismiss(1));
  await box.clear();
  expect(box.size).toBe(0);
  expect(store.data.has(storageKey('u1'))).toBe(false);
});

test('listeners hear about every change', async () => {
  const { box } = make();
  await box.load('u1');
  const seen: number[] = [];
  const off = box.subscribe(() => seen.push(box.size));
  await box.enqueue(dismiss(1));
  await box.enqueue(dismiss(2));
  await box.flush();
  off();
  await box.enqueue(dismiss(3));
  expect(seen).toEqual([1, 2, 1, 0]);
});

test('enqueue before load is an error rather than a silent loss', async () => {
  const { box } = make();
  await expect(box.enqueue(dismiss(1))).rejects.toThrow('not loaded');
});

test('detach stops using the queue but keeps it on disk for the same user to resume', async () => {
  const store = memory();
  const a = make(store);
  await a.box.load('u1');
  await a.box.enqueue(dismiss(1));
  a.box.detach();
  expect(a.box.size).toBe(0);
  await expect(a.box.enqueue(dismiss(2))).rejects.toThrow('not loaded');
  const b = make(store);
  await b.box.load('u1');
  expect(b.box.size).toBe(1);
});

test('version moves on every change, so a UI can re-read the queue only when needed', async () => {
  const { box } = make();
  await box.load('u1');
  const v0 = box.version;
  await box.enqueue(dismiss(1));
  expect(box.version).toBeGreaterThan(v0);
  const v1 = box.version;
  await box.flush();
  expect(box.version).toBeGreaterThan(v1);
});
