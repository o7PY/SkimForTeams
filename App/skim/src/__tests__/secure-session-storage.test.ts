import { CHUNK_BYTES, chunkedStorage, splitByBytes, type KeyValueStore } from '@/lib/secure-session-storage';

/** A SecureStore stand-in that enforces the same limit that makes chunking necessary (2048 bytes per value). */
function fakeStore() {
  const data = new Map<string, string>();
  const store: KeyValueStore = {
    getItemAsync: async (k) => data.get(k) ?? null,
    setItemAsync: async (k, v) => {
      if (new TextEncoder().encode(v).length > 2048) throw new Error('Value is too large');
      data.set(k, v);
    },
    deleteItemAsync: async (k) => void data.delete(k),
  };
  return { data, store };
}

const session = (n: number, filler = 'a') => JSON.stringify({ access_token: filler.repeat(n), user: { id: 'u' } });

let gen = 0;
const gens = () => `g${gen++}`; // deterministic generations for the key-layout assertions
const make = (store: KeyValueStore) => chunkedStorage(store, CHUNK_BYTES, gens);

test('a small value round-trips in one chunk', async () => {
  const { store, data } = fakeStore();
  const s = make(store);
  await s.setItem('sb', 'hello');
  expect(await s.getItem('sb')).toBe('hello');
  expect([...data.keys()].sort()).toEqual([expect.stringMatching(/^sb\.g\d+\.0$/), 'sb.n']);
});

test('a 10 KB session is split, never trips the 2 KB limit, and comes back identical', async () => {
  const { store, data } = fakeStore();
  const s = make(store);
  const v = session(10_000);
  await s.setItem('sb', v);
  expect(await s.getItem('sb')).toBe(v);
  expect(data.get('sb.n')).toMatch(new RegExp(`^g\\d+:${Math.ceil(v.length / CHUNK_BYTES)}$`));
});

test('multi-byte characters (accented names, emoji) are split by BYTES and survive the 2 KB limit', async () => {
  const { store } = fakeStore();
  const s = make(store);
  const v = session(2500, 'é') + session(500, '😀') + session(900, '日');
  await s.setItem('sb', v);
  expect(await s.getItem('sb')).toBe(v);
});

test('splitByBytes never cuts a character and never exceeds the limit', () => {
  const v = 'a😀é日'.repeat(500);
  const parts = splitByBytes(v, 100);
  expect(parts.join('')).toBe(v);
  for (const p of parts) expect(new TextEncoder().encode(p).length).toBeLessThanOrEqual(100);
  expect(splitByBytes('')).toEqual([]);
  expect(splitByBytes('😀', 3)).toEqual(['😀']); // a character bigger than the limit still comes out whole rather than looping
});

test('a shorter value replaces a longer one and the old generation is deleted', async () => {
  const { store, data } = fakeStore();
  const s = make(store);
  await s.setItem('sb', session(9000));
  await s.setItem('sb', 'short');
  expect(await s.getItem('sb')).toBe('short');
  expect(data.size).toBe(2);
});

test('removeItem deletes every chunk and the pointer', async () => {
  const { store, data } = fakeStore();
  const s = make(store);
  await s.setItem('sb', session(7000));
  await s.removeItem('sb');
  expect(await s.getItem('sb')).toBeNull();
  expect(data.size).toBe(0);
  await s.removeItem('sb'); // removing nothing is fine
});

test('an empty string stores nothing', async () => {
  const { store, data } = fakeStore();
  const s = make(store);
  await s.setItem('sb', 'x');
  await s.setItem('sb', '');
  expect(await s.getItem('sb')).toBeNull();
  expect(data.size).toBe(0);
});

test('a missing key, a corrupt pointer and a lost chunk all read as signed out', async () => {
  const { store, data } = fakeStore();
  const s = make(store);
  expect(await s.getItem('nope')).toBeNull();
  for (const bad of ['abc', '3', 'g1:0', 'g1:-2', '../x:2', 'g1:99999']) {
    data.set('bad.n', bad);
    expect(await s.getItem('bad')).toBeNull();
  }
  await s.setItem('sb', session(6000));
  const lost = [...data.keys()].find((k) => k.endsWith('.1'))!;
  data.delete(lost);
  expect(await s.getItem('sb')).toBeNull();
});

test('keys do not collide', async () => {
  const { store } = fakeStore();
  const s = make(store);
  await s.setItem('a', session(4000, 'x'));
  await s.setItem('b', session(4000, 'y'));
  expect(await s.getItem('a')).toContain('xxxx');
  expect(await s.getItem('b')).toContain('yyyy');
  await s.removeItem('a');
  expect(await s.getItem('b')).toContain('yyyy');
});

test('a write that fails halfway leaves the previous value intact and cleans up after itself', async () => {
  const { store, data } = fakeStore();
  await make(store).setItem('sb', 'the old session');
  const before = new Map(data);
  const failing: KeyValueStore = { ...store, setItemAsync: async (k, v) => { if (k.endsWith('.1')) throw new Error('disk'); return store.setItemAsync(k, v); } };
  await expect(make(failing).setItem('sb', session(5000))).rejects.toThrow('disk');
  expect(await make(store).getItem('sb')).toBe('the old session');
  expect(data).toEqual(before); // no orphaned chunks left behind
});

test('a failure while committing the pointer also leaves the old value readable', async () => {
  const { store } = fakeStore();
  await make(store).setItem('sb', 'old');
  const failing: KeyValueStore = { ...store, setItemAsync: async (k, v) => { if (k === 'sb.n') throw new Error('disk'); return store.setItemAsync(k, v); } };
  await expect(make(failing).setItem('sb', 'new')).rejects.toThrow('disk');
  expect(await make(store).getItem('sb')).toBe('old');
});
