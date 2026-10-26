import { ApiError } from '@/lib/api';
import type { KeyValue } from '@/lib/outbox';
import { CHANNELS, RegistrationGuard, registerDevice, registrationNotice, tokenKey, unregisterDevice, withTimeout, type NotificationsLike, type PushDeps } from '@/lib/push-registration';

const TOKEN = 'ExponentPushToken[abcdefghijklmnop]';

function setup(over: { granted?: boolean; canAskAgain?: boolean; grantOnAsk?: boolean; tokenError?: boolean; post?: () => Promise<unknown>; del?: () => Promise<unknown>; isDevice?: boolean; projectId?: string | undefined } = {}) {
  const calls: string[] = [];
  const data = new Map<string, string>();
  let granted = over.granted ?? false;
  const notifications: NotificationsLike = {
    AndroidImportance: { HIGH: 4 },
    setNotificationChannelAsync: async (id) => void calls.push(`channel:${id}`),
    getPermissionsAsync: async () => { calls.push('getPermissions'); return { granted, canAskAgain: over.canAskAgain ?? true }; },
    requestPermissionsAsync: async () => { calls.push('requestPermissions'); granted = over.grantOnAsk ?? true; return { granted }; },
    getExpoPushTokenAsync: async ({ projectId }) => { calls.push(`token:${projectId}`); if (over.tokenError) throw new Error('fcm'); return { data: TOKEN }; },
  };
  const store: KeyValue = { getItem: async (k) => data.get(k) ?? null, setItem: async (k, v) => void data.set(k, v), removeItem: async (k) => void data.delete(k) };
  const api = {
    post: jest.fn(async (_p: string, _b?: unknown) => { calls.push('post'); return (over.post ? await over.post() : { ok: true }) as never; }),
    delete: jest.fn(async (_p: string) => { calls.push('delete'); return (over.del ? await over.del() : undefined) as never; }),
  };
  const deps: PushDeps = { notifications, api, store, userId: 'u1', platform: 'android', isDevice: over.isDevice ?? true, projectId: 'projectId' in over ? over.projectId : 'proj-1' };
  return { deps, calls, data, api };
}

test('already granted: channels first, then the token, then the server; the token is remembered', async () => {
  const s = setup({ granted: true });
  expect(await registerDevice(s.deps, { ask: false })).toEqual({ status: 'registered', token: TOKEN });
  expect(s.calls).toEqual(['channel:reminders', 'channel:changes', 'getPermissions', 'token:proj-1', 'post']);
  expect(s.api.post).toHaveBeenCalledWith('/devices', { expo_push_token: TOKEN, platform: 'android' });
  expect(s.data.get(tokenKey('u1'))).toBe(TOKEN);
});

test('channels exist BEFORE the permission prompt (Android 13 shows no prompt otherwise)', async () => {
  const s = setup({ granted: false });
  await registerDevice(s.deps, { ask: true });
  expect(s.calls.indexOf('channel:changes')).toBeLessThan(s.calls.indexOf('requestPermissions'));
  expect(CHANNELS.map((c) => c.id)).toEqual(['reminders', 'changes']);
});

test('without permission and not allowed to ask, nothing is asked and nothing is registered', async () => {
  const s = setup({ granted: false });
  expect(await registerDevice(s.deps, { ask: false })).toEqual({ status: 'needs_permission' });
  expect(s.calls).not.toContain('requestPermissions');
  expect(s.calls).not.toContain('post');
});

test('asking: the student says yes -> registered; says no -> denied (and the app can ask again if the OS allows)', async () => {
  const yes = setup({ granted: false, grantOnAsk: true });
  expect((await registerDevice(yes.deps, { ask: true })).status).toBe('registered');
  const no = setup({ granted: false, grantOnAsk: false });
  expect(await registerDevice(no.deps, { ask: true })).toEqual({ status: 'denied', canAskAgain: true });
  expect(no.api.post).not.toHaveBeenCalled();
});

test('permanently denied: the OS dialog is not even attempted', async () => {
  const s = setup({ granted: false, canAskAgain: false });
  expect(await registerDevice(s.deps, { ask: true })).toEqual({ status: 'denied', canAskAgain: false });
  expect(s.calls).not.toContain('requestPermissions');
  expect(registrationNotice({ status: 'denied', canAskAgain: false })).toContain('phone settings');
  expect(registrationNotice({ status: 'denied', canAskAgain: true })).toBeNull();
});

test.each([
  ['an emulator or non-device', { isDevice: false }, 'not_a_device'],
  ['a build without an EAS project id', { projectId: undefined }, 'no_project_id'],
  ['a token request that fails (offline or FCM not set up)', { granted: true, tokenError: true }, 'token_failed'],
])('%s is "unavailable", never a crash', async (_n, over, reason) => {
  const s = setup(over);
  expect(await registerDevice(s.deps, { ask: true })).toEqual({ status: 'unavailable', reason });
  expect(s.api.post).not.toHaveBeenCalled();
});

test('the server answers: 409 taken, 429 too many, network down, anything else', async () => {
  const mk = (status: number) => setup({ granted: true, post: async () => { throw new ApiError(status, null); } });
  expect((await registerDevice(mk(409).deps, { ask: false })).status).toBe('taken');
  expect((await registerDevice(mk(429).deps, { ask: false })).status).toBe('too_many_devices');
  expect(await registerDevice(mk(0).deps, { ask: false })).toEqual({ status: 'offline', token: TOKEN });
  expect(await registerDevice(mk(400).deps, { ask: false })).toEqual({ status: 'unavailable', reason: 'rejected' });
  const taken = mk(409);
  await registerDevice(taken.deps, { ask: false });
  expect(taken.data.has(tokenKey('u1'))).toBe(false); // a token we do not own is not remembered
});

test('notices: only the ones the student can act on', () => {
  expect(registrationNotice({ status: 'taken' })).toContain('Sign out there first');
  expect(registrationNotice({ status: 'too_many_devices' })).toContain('10 phones');
  expect(registrationNotice({ status: 'offline', token: TOKEN })).toBeNull();
  expect(registrationNotice({ status: 'needs_permission' })).toBeNull();
  expect(registrationNotice({ status: 'registered', token: TOKEN })).toBeNull();
});

test('unregister: deletes the remembered token (url-encoded) and forgets it', async () => {
  const s = setup({ granted: true });
  await registerDevice(s.deps, { ask: false });
  expect(await unregisterDevice(s.deps)).toBe('ok');
  expect(s.api.delete).toHaveBeenCalledWith(`/devices/${encodeURIComponent(TOKEN)}`);
  expect(s.api.delete).toHaveBeenCalledTimes(1); // the remembered token and the OS token are the same one: asked once
  expect(s.data.has(tokenKey('u1'))).toBe(false);
});

test('a LOST local copy cannot leave the phone linked: the token is asked from the OS instead', async () => {
  const s = setup({ granted: true });
  await registerDevice(s.deps, { ask: false });
  s.data.clear(); // storage wiped / never written
  expect(await unregisterDevice(s.deps)).toBe('ok');
  expect(s.api.delete).toHaveBeenCalledWith(`/devices/${encodeURIComponent(TOKEN)}`);
});

test('"none" only when there is truly nothing: no remembered token and no permission or no OS token', async () => {
  const noPerm = setup({ granted: false });
  expect(await unregisterDevice(noPerm.deps)).toBe('none');
  const noToken = setup({ granted: true, tokenError: true });
  expect(await unregisterDevice(noToken.deps)).toBe('none');
  const emulator = setup({ granted: true, isDevice: false });
  expect(await unregisterDevice(emulator.deps)).toBe('none');
  expect(noPerm.api.delete).not.toHaveBeenCalled();
});

test('a remembered token is still removed when the OS cannot be asked (offline)', async () => {
  const s = setup({ granted: true });
  await registerDevice(s.deps, { ask: false });
  const offline = { ...s.deps, notifications: { ...s.deps.notifications, getExpoPushTokenAsync: async () => { throw new Error('offline'); } } };
  expect(await unregisterDevice(offline)).toBe('ok');
  expect(s.api.delete).toHaveBeenCalledTimes(1);
});

test('if the remembered token differs from the OS one (the token rotated), both are removed', async () => {
  const s = setup({ granted: true });
  s.data.set(tokenKey('u1'), 'ExponentPushToken[oldoldoldoldold]');
  expect(await unregisterDevice(s.deps)).toBe('ok');
  expect(s.api.delete).toHaveBeenCalledTimes(2);
  expect(s.data.has(tokenKey('u1'))).toBe(false);
});

test.each([
  ['the server no longer knows it (404)', new ApiError(404, null), 'ok', false],
  ['no connection', new ApiError(0, null), 'offline', true],
  ['a server error', new ApiError(500, null), 'error', true],
])('unregister when %s', async (_n, e, result, kept) => {
  const s = setup({ granted: true, del: async () => { throw e; } });
  await registerDevice(s.deps, { ask: false });
  expect(await unregisterDevice(s.deps)).toBe(result);
  expect(s.data.has(tokenKey('u1'))).toBe(kept); // kept so a later attempt can still unregister it
});

test('one failing delete does not stop the other token being removed', async () => {
  const s = setup({ granted: true });
  s.data.set(tokenKey('u1'), 'ExponentPushToken[oldoldoldoldold]');
  let n = 0;
  s.api.delete.mockImplementation(async () => { if (n++ === 0) throw new ApiError(500, null); return undefined as never; });
  expect(await unregisterDevice(s.deps)).toBe('error');
  expect(s.api.delete).toHaveBeenCalledTimes(2);
});

test('the remembered token is per account', async () => {
  const s = setup({ granted: false });
  s.data.set(tokenKey('u1'), TOKEN);
  expect(await unregisterDevice({ ...s.deps, userId: 'someone-else' })).toBe('none');
  expect(s.api.delete).not.toHaveBeenCalled();
});

// ---- the loop that flooded a real phone: registering asks for a token, the token event fires, the listener registers again...
function clock(start = 1_000_000) {
  let t = start;
  return { now: () => t, advance: (ms: number) => void (t += ms) };
}

test('the register -> token event -> register loop is cut: the same native token twice does nothing', () => {
  const c = clock();
  const g = new RegistrationGuard({ now: c.now });
  expect(g.tryStart('start')).toBe(true);
  g.done();
  c.advance(11_000);
  expect(g.tryStart('token', 'native-A')).toBe(true); // a first, real token event
  g.done();
  let extra = 0;
  for (let i = 0; i < 1000; i++) {
    c.advance(11_000);
    if (g.tryStart('token', 'native-A')) extra++; // the event fired by our own getExpoPushTokenAsync: same token
  }
  expect(extra).toBe(0);
});

test('a token event while an attempt is running, or too soon after one, is ignored', () => {
  const c = clock();
  const g = new RegistrationGuard({ now: c.now });
  expect(g.tryStart('start')).toBe(true);
  expect(g.tryStart('token', 'native-A')).toBe(false); // in flight
  g.done();
  expect(g.tryStart('token', 'native-B')).toBe(false); // finished, but under 10 s ago
  c.advance(10_001);
  expect(g.tryStart('token', 'native-B')).toBe(true);  // a genuinely new token, and enough time passed
});

test('a token that changes every time still cannot flood: at most one attempt per 10 s', () => {
  const c = clock();
  const g = new RegistrationGuard({ now: c.now });
  let runs = 0;
  for (let i = 0; i < 600; i++) {
    c.advance(1_000); // one event per second for 10 minutes
    if (g.tryStart('token', `native-${i}`)) { runs++; g.done(); }
  }
  expect(runs).toBeLessThanOrEqual(60);
});

test('quiet start attempts are limited to one a minute; a deliberate ask always goes through', () => {
  const c = clock();
  const g = new RegistrationGuard({ now: c.now });
  expect(g.tryStart('start')).toBe(true);
  g.done();
  c.advance(30_000);
  expect(g.tryStart('start')).toBe(false);
  expect(g.tryStart('ask')).toBe(true);  // right after the first join: the student expects the prompt
  c.advance(60_001);
  g.done();
  expect(g.tryStart('start')).toBe(true);
});

test('once sign-out begins nothing can register again, not even a deliberate ask', () => {
  const c = clock();
  const g = new RegistrationGuard({ now: c.now });
  g.suspend();
  for (const r of ['start', 'token', 'ask'] as const) expect(g.tryStart(r, 'x')).toBe(false);
});

test('withTimeout: the value if it is quick, the fallback if it is not or if it throws', async () => {
  jest.useFakeTimers();
  try {
    expect(await withTimeout(Promise.resolve('ok'), 100, 'late')).toBe('ok');
    const slow = withTimeout(new Promise<string>(() => undefined), 100, 'late');
    await jest.advanceTimersByTimeAsync(101);
    expect(await slow).toBe('late');
    expect(await withTimeout(Promise.reject(new Error('x')), 100, 'late')).toBe('late');
  } finally {
    jest.useRealTimers();
  }
});

test('a REAL token change that arrives while busy or too soon is kept and handed back once, not lost', () => {
  const c = clock();
  const g = new RegistrationGuard({ now: c.now });
  expect(g.tryStart('start')).toBe(true);
  expect(g.tryStart('token', 'native-NEW')).toBe(false); // busy
  g.done();
  expect(g.consumePending()).toBe('native-NEW');
  expect(g.consumePending()).toBeNull();                 // handed back once
  c.advance(10_001);
  expect(g.tryStart('token', 'native-NEW')).toBe(true);  // the retry goes through
  g.done();
  c.advance(10_001);
  expect(g.tryStart('token', 'native-NEW')).toBe(false); // and its own echo does not
});

test('a pending token that turns out to be the one already registered is dropped, and sign-out clears it', () => {
  const c = clock();
  const g = new RegistrationGuard({ now: c.now });
  g.tryStart('token', 'native-A');
  g.done();
  g.tryStart('token', 'native-B'); // too soon
  c.advance(10_001);
  g.tryStart('token', 'native-B');
  g.done();
  expect(g.consumePending()).toBeNull();
  g.tryStart('token', 'native-C');
  g.suspend();
  expect(g.consumePending()).toBeNull();
});
