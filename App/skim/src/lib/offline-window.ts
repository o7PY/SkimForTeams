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

/** Wait (checking every `stepMs`, for at most `maxMs`) until `quiet()` is true. Resolves either way: a busy screen must delay the
 *  background loader, never stop it. */
export async function waitUntilQuiet(quiet: () => boolean, maxMs: number, stepMs = 150): Promise<void> {
  const until = Date.now() + maxMs;
  while (!quiet() && Date.now() < until) await new Promise((r) => setTimeout(r, stepMs));
}

// ---------------------------------------------------------------- keeping the saved cache a safe size
// On Android a single AsyncStorage value above about 2 MB cannot be read back, and the whole query cache is one value. What is saved
// is trimmed in this order: each team's card list keeps only its first page; when the total is still over `maxChars`, the oldest team
// lists go, then the oldest opened-card details (they can be fetched again). Other lists and settings are never dropped.
type SavedQuery = { queryKey: readonly unknown[]; state: { dataUpdatedAt?: number; data?: unknown } };
type Saved = { clientState: { queries: SavedQuery[] } };
export const TEAM_CARDS_KEY = 'team-cards';

type Paged = { pages?: unknown[]; pageParams?: unknown[] };
/** A team's card list as it is saved: the first page only (the rest is one scroll away when the phone is online). */
function firstPageOnly(q: SavedQuery): SavedQuery {
  const d = q.state.data as Paged | undefined;
  if (q.queryKey[0] !== TEAM_CARDS_KEY || !d || !Array.isArray(d.pages) || d.pages.length <= 1) return q;
  return { ...q, state: { ...q.state, data: { ...d, pages: d.pages.slice(0, 1), pageParams: (d.pageParams ?? []).slice(0, 1) } } };
}

export function pruneForSize<T extends Saved>(client: T, maxChars: number): T {
  const trimmed = client.clientState.queries.map(firstPageOnly);
  const changed = trimmed.some((q, i) => q !== client.clientState.queries[i]);
  const base = changed ? { ...client, clientState: { ...client.clientState, queries: trimmed } } : client;
  let size = JSON.stringify(base).length;
  if (size <= maxChars) return base;
  const oldest = (key: string) =>
    base.clientState.queries.filter((q) => q.queryKey[0] === key).sort((a, b) => (a.state.dataUpdatedAt ?? 0) - (b.state.dataUpdatedAt ?? 0));
  const drop = new Set<SavedQuery>();
  for (const q of [...oldest(TEAM_CARDS_KEY), ...oldest('item')]) {
    if (size <= maxChars) break;
    size -= JSON.stringify(q).length + 1;
    drop.add(q);
  }
  return { ...base, clientState: { ...base.clientState, queries: base.clientState.queries.filter((q) => !drop.has(q)) } };
}
export const MAX_SAVED_CHARS = 1_500_000;
