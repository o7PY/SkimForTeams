// What stays on the phone for offline use. The student asked for the last 50 cards, fully readable without a connection, kept until a
// 51st arrives; starred cards are always kept. Pure, so it can be tested without a phone.
export const WINDOW = 50;
export const MAX_STARRED_KEPT = 100;

export type Slim = { id: number; occurred_at: string };
const time = (c: Slim) => {
  const t = Date.parse(c.occurred_at);
  return Number.isNaN(t) ? 0 : t;
};
const newestFirst = (a: Slim, b: Slim) => time(b) - time(a) || b.id - a.id;

/** The cards whose full detail is kept: the `limit` newest of everything the student has (unread deck + history), then every starred card. */
export function keepIds(deck: readonly Slim[], history: readonly Slim[], starred: readonly Slim[], limit: number = WINDOW): number[] {
  const seen = new Set<number>();
  const keep: number[] = [];
  for (const c of [...deck, ...history].sort(newestFirst)) {
    if (seen.has(c.id)) continue;
    seen.add(c.id);
    if (keep.length < limit) keep.push(c.id);
  }
  let extra = 0;
  for (const c of [...starred].sort(newestFirst)) {
    if (extra >= MAX_STARRED_KEPT) break;
    if (!keep.includes(c.id)) {
      keep.push(c.id);
      extra++;
    }
  }
  return keep;
}

/** Cached detail ids that are no longer wanted: the oldest ones once newer cards have pushed them out of the window. */
export const evictIds = (cached: readonly number[], keep: readonly number[]): number[] => {
  const k = new Set(keep);
  return cached.filter((id) => !k.has(id));
};

/** Run `worker` over `items` with at most `n` at a time. A failing item never stops the others. */
export async function runPool<T>(items: readonly T[], n: number, worker: (item: T) => Promise<void>): Promise<void> {
  let next = 0;
  const lane = async () => {
    while (next < items.length) {
      const item = items[next++];
      try {
        await worker(item);
      } catch {
        // one card failing to load must not stop the rest
      }
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(n, items.length)) }, lane));
}

// ---------------------------------------------------------------- keeping the saved cache a safe size
// On Android a single AsyncStorage value above about 2 MB cannot be read back, and the whole query cache is one value. When it grows
// past `maxChars`, the oldest opened-card details go first (they can be fetched again); lists and settings are never dropped.
type SavedQuery = { queryKey: readonly unknown[]; state: { dataUpdatedAt?: number } };
type Saved = { clientState: { queries: SavedQuery[] } };

export function pruneForSize<T extends Saved>(client: T, maxChars: number): T {
  let size = JSON.stringify(client).length;
  if (size <= maxChars) return client;
  const oldestDetailsFirst = client.clientState.queries.filter((q) => q.queryKey[0] === 'item').sort((a, b) => (a.state.dataUpdatedAt ?? 0) - (b.state.dataUpdatedAt ?? 0));
  const drop = new Set<SavedQuery>();
  for (const q of oldestDetailsFirst) {
    if (size <= maxChars) break;
    size -= JSON.stringify(q).length + 1;
    drop.add(q);
  }
  return { ...client, clientState: { ...client.clientState, queries: client.clientState.queries.filter((q) => !drop.has(q)) } };
}
export const MAX_SAVED_CHARS = 1_500_000;
