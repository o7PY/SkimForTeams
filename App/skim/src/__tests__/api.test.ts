import { ApiError, createApi, type TokenSource } from '@/lib/api';

type Call = { url: string; init: RequestInit };

function server(responses: (Response | Error)[]) {
  const calls: Call[] = [];
  const fetchImpl = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    const r = responses.shift();
    if (!r) throw new Error('unexpected extra request');
    if (r instanceof Error) throw r;
    return r;
  }) as unknown as typeof fetch;
  return { calls, fetchImpl };
}

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const auth = (c: Call) => (c.init.headers as Record<string, string>).Authorization;

function tokens(over: Partial<TokenSource> = {}) {
  const t = {
    getToken: jest.fn(async () => 'tok-1' as string | null),
    refresh: jest.fn(async () => 'tok-2' as string | null),
    onSignedOut: jest.fn(),
    ...over,
  };
  return t;
}

test('sends the bearer token and a JSON body to /api/v1', async () => {
  const s = server([json(200, { ok: true })]);
  const api = createApi({ baseUrl: 'https://x.example', tokens: tokens(), fetchImpl: s.fetchImpl });
  await expect(api.post('/devices', { a: 1 })).resolves.toEqual({ ok: true });
  expect(s.calls[0].url).toBe('https://x.example/api/v1/devices');
  expect(s.calls[0].init.method).toBe('POST');
  expect(auth(s.calls[0])).toBe('Bearer tok-1');
  expect((s.calls[0].init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  expect(s.calls[0].init.body).toBe('{"a":1}');
});

test('GET has no body and no content type; no token means no Authorization header', async () => {
  const s = server([json(200, {})]);
  const api = createApi({ baseUrl: 'https://x', tokens: tokens({ getToken: async () => null }), fetchImpl: s.fetchImpl });
  await api.get('/me');
  expect(s.calls[0].init.body).toBeUndefined();
  expect(s.calls[0].init.headers).not.toHaveProperty('Authorization');
  expect(s.calls[0].init.headers).not.toHaveProperty('Content-Type');
});

test('a 401 refreshes once and retries with the new token', async () => {
  const s = server([json(401, { error: 'unauthorized' }), json(200, { id: 'u' })]);
  const t = tokens();
  const api = createApi({ baseUrl: 'https://x', tokens: t, fetchImpl: s.fetchImpl });
  await expect(api.get('/me')).resolves.toEqual({ id: 'u' });
  expect(t.refresh).toHaveBeenCalledTimes(1);
  expect(s.calls.map(auth)).toEqual(['Bearer tok-1', 'Bearer tok-2']);
  expect(t.onSignedOut).not.toHaveBeenCalled();
});

test('a 401 that survives the refresh ends the session and does not loop', async () => {
  const s = server([json(401, {}), json(401, {})]);
  const t = tokens();
  const api = createApi({ baseUrl: 'https://x', tokens: t, fetchImpl: s.fetchImpl });
  await expect(api.get('/me')).rejects.toMatchObject({ status: 401, message: 'Your session has ended. Please sign in again.' });
  expect(s.calls).toHaveLength(2);
  expect(t.onSignedOut).toHaveBeenCalledTimes(1);
});

test.each([[null], ['tok-1']])('when the refresh gives %p there is no retry, just sign-out', async (fresh) => {
  const s = server([json(401, {})]);
  const t = tokens({ refresh: async () => fresh });
  const api = createApi({ baseUrl: 'https://x', tokens: t, fetchImpl: s.fetchImpl });
  await expect(api.get('/me')).rejects.toBeInstanceOf(ApiError);
  expect(s.calls).toHaveLength(1);
  expect(t.onSignedOut).toHaveBeenCalledTimes(1);
});

test('204 resolves to undefined', async () => {
  const s = server([new Response(null, { status: 204 })]);
  const api = createApi({ baseUrl: 'https://x', tokens: tokens(), fetchImpl: s.fetchImpl });
  await expect(api.delete('/me')).resolves.toBeUndefined();
});

test('errors keep the status and the server body (409 join, 400 details, 429)', async () => {
  const s = server([json(409, { error: 'already joined', team: { id: 'T1' } }), json(400, { error: 'invalid settings', details: ['history_limit: x'] }), json(429, { error: 'too many attempts' })]);
  const api = createApi({ baseUrl: 'https://x', tokens: tokens(), fetchImpl: s.fetchImpl });
  await expect(api.post('/subscriptions', { code: 'A' })).rejects.toMatchObject({ status: 409, body: { team: { id: 'T1' } } });
  await expect(api.patch('/me/settings', {})).rejects.toMatchObject({ status: 400, body: { details: ['history_limit: x'] } });
  const e = await api.post('/subscriptions', { code: 'B' }).catch((x: ApiError) => x);
  expect(e).toMatchObject({ status: 429, message: 'too many attempts', isNetwork: false });
});

test('a non-JSON error page becomes an ApiError with a null body, not a crash', async () => {
  const s = server([new Response('<html>bad gateway</html>', { status: 502 })]);
  const api = createApi({ baseUrl: 'https://x', tokens: tokens(), fetchImpl: s.fetchImpl });
  await expect(api.get('/deck')).rejects.toMatchObject({ status: 502, body: null, message: 'HTTP 502' });
});

test('a dead network is an ApiError with status 0', async () => {
  const s = server([new TypeError('Network request failed')]);
  const api = createApi({ baseUrl: 'https://x', tokens: tokens(), fetchImpl: s.fetchImpl });
  const e = await api.get('/deck').catch((x: ApiError) => x);
  expect(e).toBeInstanceOf(ApiError);
  expect(e).toMatchObject({ status: 0, isNetwork: true, message: 'No connection' });
});

test('a request that takes too long is aborted and reported as a timeout', async () => {
  jest.useFakeTimers();
  try {
    const hang = jest.fn((_u: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_res, rej) => init!.signal!.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' }))))) as unknown as typeof fetch;
    const api = createApi({ baseUrl: 'https://x', tokens: tokens(), fetchImpl: hang, timeoutMs: 5000 });
    const p = api.get('/deck').catch((x: ApiError) => x);
    await jest.advanceTimersByTimeAsync(5001);
    expect(await p).toMatchObject({ status: 0, message: 'The request timed out' });
  } finally {
    jest.useRealTimers();
  }
});

test('error messages never contain the token', async () => {
  const s = server([json(500, { error: 'boom' })]);
  const api = createApi({ baseUrl: 'https://x', tokens: tokens(), fetchImpl: s.fetchImpl });
  const e = (await api.get('/me').catch((x: ApiError) => x)) as ApiError;
  expect(JSON.stringify([e.message, e.body])).not.toContain('tok-');
});
