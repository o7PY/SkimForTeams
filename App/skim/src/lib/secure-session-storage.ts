// supabase-js keeps its session as one JSON string (2-4 KB), but SecureStore refuses values over 2048 BYTES on Android.
// This adapter splits the value into chunks of at most CHUNK_BYTES (cut on character boundaries, so multi-byte text such as an
// accented name survives) and writes every save under a fresh "generation". The pointer `key.n` = "<generation>:<count>" is written
// last, so a crash or a failed write midway leaves the previous value fully intact. Pure: the store is injected.
export type KeyValueStore = {
  getItemAsync(key: string): Promise<string | null>;
  setItemAsync(key: string, value: string): Promise<void>;
  deleteItemAsync(key: string): Promise<void>;
};

export const CHUNK_BYTES = 1800;

function byteLength(codePoint: number): number {
  return codePoint < 0x80 ? 1 : codePoint < 0x800 ? 2 : codePoint < 0x10000 ? 3 : 4;
}

/** Split into pieces of at most `limit` UTF-8 bytes without cutting a character in half. */
export function splitByBytes(value: string, limit: number = CHUNK_BYTES): string[] {
  const parts: string[] = [];
  let cur = '';
  let size = 0;
  for (const ch of value) {
    const b = byteLength(ch.codePointAt(0)!);
    if (cur && size + b > limit) {
      parts.push(cur);
      cur = '';
      size = 0;
    }
    cur += ch;
    size += b;
  }
  if (cur) parts.push(cur);
  return parts;
}

type Pointer = { gen: string; count: number };

function parsePointer(raw: string | null): Pointer | null {
  const m = /^([a-z0-9]{1,16}):(\d{1,4})$/.exec(raw ?? '');
  return m && Number(m[2]) > 0 ? { gen: m[1], count: Number(m[2]) } : null;
}

export function chunkedStorage(store: KeyValueStore, chunkBytes: number = CHUNK_BYTES, newGeneration: () => string = () => Date.now().toString(36) + Math.floor(Math.random() * 1296).toString(36)) {
  const pointerKey = (k: string) => `${k}.n`;
  const partKey = (k: string, gen: string, i: number) => `${k}.${gen}.${i}`;
  const readPointer = async (k: string) => parsePointer(await store.getItemAsync(pointerKey(k)));
  const deleteParts = async (k: string, p: Pointer) => {
    for (let i = 0; i < p.count; i++) await store.deleteItemAsync(partKey(k, p.gen, i));
  };

  return {
    async getItem(key: string): Promise<string | null> {
      const p = await readPointer(key);
      if (!p) return null;
      const parts: string[] = [];
      for (let i = 0; i < p.count; i++) {
        const part = await store.getItemAsync(partKey(key, p.gen, i));
        if (part === null) return null; // a chunk is gone: treat as signed out rather than hand back half a session
        parts.push(part);
      }
      return parts.join('');
    },
    async setItem(key: string, value: string): Promise<void> {
      const parts = splitByBytes(value, chunkBytes);
      if (parts.length === 0) return this.removeItem(key); // an empty string stores nothing
      const old = await readPointer(key);
      const fresh: Pointer = { gen: newGeneration(), count: parts.length };
      try {
        for (let i = 0; i < parts.length; i++) await store.setItemAsync(partKey(key, fresh.gen, i), parts[i]);
        await store.setItemAsync(pointerKey(key), `${fresh.gen}:${fresh.count}`); // commit
      } catch (e) {
        await deleteParts(key, fresh).catch(() => undefined); // best effort; the old value was never touched
        throw e;
      }
      if (old) await deleteParts(key, old).catch(() => undefined);
    },
    async removeItem(key: string): Promise<void> {
      const p = await readPointer(key);
      await store.deleteItemAsync(pointerKey(key)); // the pointer first: a half-finished removal then reads as signed out
      if (p) await deleteParts(key, p);
    },
  };
}
