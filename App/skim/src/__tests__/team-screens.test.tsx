// The Teams tab and one team's cards.
import { act, create, type ReactTestInstance } from 'react-test-renderer';

import { messageCard } from './fixtures';
import Teams from '../app/(tabs)/teams';
import TeamCards from '../app/team/[id]';

const mockPush = jest.fn();
const mockFetchNext = jest.fn();
const mockRefetch = jest.fn();
type Subs = { data: unknown; isPending: boolean; isError: boolean; isRefetching: boolean; refetch: () => void };
let mockSubs: Subs;
let mockTeam: { cards: unknown[]; data: unknown; isPending: boolean; isError: boolean; isRefetching: boolean; isFetchingNextPage: boolean; hasNextPage: boolean; refetch: () => void; fetchNextPage: () => void };

jest.mock('expo-router', () => ({
  router: { push: (...a: unknown[]) => mockPush(...a), back: jest.fn(), canGoBack: () => true, replace: jest.fn() },
  useLocalSearchParams: () => ({ id: 'T1' }),
}));
jest.mock('react-native-safe-area-context', () => ({ SafeAreaView: jest.requireActual('react-native').View }));
jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});
jest.mock('@/ui/atmosphere', () => ({ Atmosphere: () => null }));
jest.mock('@/lib/queries', () => ({ useSubscriptions: () => mockSubs, useTeamCards: () => mockTeam }));

const isText = (n: ReactTestInstance) => (n.type as unknown) === 'Text';
const render = (el: React.ReactElement) => { let r!: ReturnType<typeof create>; act(() => { r = create(el); }); return r; };
const words = (r: ReturnType<typeof create>) => r.root.findAll(isText).map((n) => n.children.join('')).join(' | ');
const press = (r: ReturnType<typeof create>, label: string) => act(() => r.root.findAll((n) => n.props.accessibilityLabel === label && typeof n.props.onPress === 'function')[0].props.onPress());
const team = (id: string, name: string, over = {}) => ({ id, name, joined_at: '2026-10-01T00:00:00+05:30', muted: false, ended: false, ...over });

beforeEach(() => {
  jest.clearAllMocks();
  mockSubs = { data: [], isPending: false, isError: false, isRefetching: false, refetch: mockRefetch };
  mockTeam = { cards: [], data: { pages: [] }, isPending: false, isError: false, isRefetching: false, isFetchingNextPage: false, hasNextPage: false, refetch: mockRefetch, fetchNextPage: mockFetchNext };
});

describe('Teams tab', () => {
  test('lists every team, marks an ended and a muted one, and opens a team on tap', () => {
    mockSubs = { ...mockSubs, data: [team('T1', 'CS 201'), team('T2', 'PH 105', { ended: true }), team('T3', 'MA 211', { muted: true })] };
    const r = render(<Teams />);
    const t = words(r);
    expect(t).toContain('3 courses');
    for (const n of ['CS 201', 'PH 105', 'MA 211']) expect(t).toContain(n);
    expect(t).toContain('Ended');
    expect(t).toContain('[icon:bell-off-outline]');
    press(r, 'CS 201. Show its cards');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/team/[id]', params: { id: 'T1' } });
  });
  test('no teams: says how to join and offers the join screen', () => {
    const r = render(<Teams />);
    expect(words(r)).toContain('No teams yet');
    press(r, 'Join a team');
    expect(mockPush).toHaveBeenCalledWith('/join');
  });
  test('loading renders, and a failure offers a retry that retries', () => {
    mockSubs = { ...mockSubs, isPending: true, data: undefined };
    expect(() => render(<Teams />)).not.toThrow();
    mockSubs = { ...mockSubs, isPending: false, isError: true, data: undefined };
    const r = render(<Teams />);
    expect(words(r)).toContain('Can’t load your teams');
    press(r, 'Try again');
    expect(mockRefetch).toHaveBeenCalledTimes(1);
  });
});

describe('a team’s cards', () => {
  test('shows the team name and every card, and a card opens its detail', () => {
    mockSubs = { ...mockSubs, data: [team('T1', 'CS 201')] };
    const a = messageCard({ id: 21 }), b = messageCard({ id: 22 });
    mockTeam = { ...mockTeam, cards: [a, b], data: { pages: [{ cards: [a, b] }] } };
    const r = render(<TeamCards />);
    expect(words(r)).toContain('CS 201');
    expect(words(r)).toContain('2 cards');
    act(() => r.root.findAll((n) => typeof n.props.accessibilityLabel === 'string' && n.props.accessibilityLabel.startsWith('Course') === false && n.props.accessibilityRole === 'button' && typeof n.props.onPress === 'function' && /Quiz|\./.test(n.props.accessibilityLabel))[0].props.onPress());
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/item/[id]', params: { id: '21' } });
  });
  test('empty, loading and failing states; failing offers a retry', () => {
    expect(words(render(<TeamCards />))).toContain('No cards yet');
    mockTeam = { ...mockTeam, isPending: true, data: undefined };
    expect(() => render(<TeamCards />)).not.toThrow();
    mockTeam = { ...mockTeam, isPending: false, isError: true, data: undefined };
    const r = render(<TeamCards />);
    expect(words(r)).toContain('Can’t load this team’s cards');
    press(r, 'Try again');
    expect(mockRefetch).toHaveBeenCalledTimes(1);
  });
  test('reaching the end of the list asks for the next page, once, and only if there is one', () => {
    const a = messageCard({ id: 31 });
    mockTeam = { ...mockTeam, cards: [a], data: { pages: [{ cards: [a] }] }, hasNextPage: true };
    const r = render(<TeamCards />);
    const list = r.root.findAll((n) => typeof n.props.onEndReached === 'function')[0];
    act(() => list.props.onEndReached());
    expect(mockFetchNext).toHaveBeenCalledTimes(1);
    mockTeam = { ...mockTeam, hasNextPage: false };
    const r2 = render(<TeamCards />);
    act(() => r2.root.findAll((n) => typeof n.props.onEndReached === 'function')[0].props.onEndReached());
    expect(mockFetchNext).toHaveBeenCalledTimes(1);
  });
});
