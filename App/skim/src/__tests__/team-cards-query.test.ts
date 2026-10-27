import { TEAM_CARDS_PAGE, teamCardsOptions } from '@/lib/query-options';
import type { Api } from '@/lib/api';

jest.mock('@react-native-async-storage/async-storage', () => jest.requireActual('@react-native-async-storage/async-storage/jest/async-storage-mock'));

const run = (api: Api, team: string, pageParam: string | null) =>
  (teamCardsOptions(api, team).queryFn as (c: { pageParam: string | null }) => Promise<unknown>)({ pageParam });

test('the first page asks for one page of a team, later pages pass the cursor', async () => {
  const get = jest.fn(async () => ({ cards: [], next_cursor: null }));
  const api = { get } as unknown as Api;
  await run(api, 'T1', null);
  await run(api, 'T1', 'abc_-');
  expect(get.mock.calls).toEqual([[`/teams/T1/cards?limit=${TEAM_CARDS_PAGE}`], [`/teams/T1/cards?limit=${TEAM_CARDS_PAGE}&cursor=abc_-`]]);
});

test('a team id is escaped, and the next page comes from the server cursor', async () => {
  const get = jest.fn(async (_path: string) => ({ cards: [], next_cursor: null }));
  await run({ get } as unknown as Api, '19:abc@thread.tacv2', null);
  expect(get.mock.calls[0][0]).toBe(`/teams/${encodeURIComponent('19:abc@thread.tacv2')}/cards?limit=${TEAM_CARDS_PAGE}`);
  const o = teamCardsOptions({ get } as unknown as Api, 'T1');
  expect(o.getNextPageParam({ cards: [], next_cursor: 'zzz', total: 0 }, [], null, [])).toBe('zzz');
  expect(o.getNextPageParam({ cards: [], next_cursor: null, total: 0 }, [], null, [])).toBeUndefined();
});
