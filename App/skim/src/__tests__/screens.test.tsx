import { act, create, type ReactTestInstance } from 'react-test-renderer';

import { DEFAULTS, toggleType } from '@/lib/settings';

import History from '../app/(tabs)/history';
import Important from '../app/(tabs)/important';
import Starred from '../app/(tabs)/starred';
import { changeCard, documentCard, event, messageCard } from './fixtures';

// ---- everything outside the screens is replaced, so what runs here is the screens' own code
const mockPush = jest.fn();
const mockUpdate = jest.fn(async () => undefined);
const mockPutBack = jest.fn(async () => undefined);
const mockUnstar = jest.fn(async () => undefined);
let mockSettings = DEFAULTS;
let mockList: { cards: unknown[]; isPending: boolean; isError: boolean; data: unknown; isRefetching: boolean; refetch: () => void } = { cards: [], isPending: false, isError: false, data: {}, isRefetching: false, refetch: jest.fn() };

jest.mock('expo-router', () => ({ router: { push: (...a: unknown[]) => mockPush(...a), back: jest.fn(), canGoBack: () => true, replace: jest.fn() }, Redirect: () => null }));
jest.mock('react-native-safe-area-context', () => ({ SafeAreaView: jest.requireActual('react-native').View }));
jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});
jest.mock('@/ui/atmosphere', () => ({ Atmosphere: () => null }));
jest.mock('@/lib/use-settings', () => ({ useSettings: () => ({ settings: mockSettings, ready: true, update: mockUpdate }) }));
jest.mock('@/lib/lists', () => ({ useHistory: () => mockList, useStarred: () => mockList, useListActions: () => ({ putBack: mockPutBack, unstar: mockUnstar }) }));

const isText = (n: ReactTestInstance) => (n.type as unknown) === 'Text';
const render = (el: React.ReactElement) => { let r!: ReturnType<typeof create>; act(() => { r = create(el); }); return r; };
const words = (r: ReturnType<typeof create>) => r.root.findAll(isText).map((n) => n.children.join('')).join(' | ');
const checkboxes = (r: ReturnType<typeof create>) => r.root.findAll((n) => typeof n.type === 'string' && n.props.accessibilityRole === 'checkbox');
const press = (r: ReturnType<typeof create>, label: string) => act(() => r.root.findAll((n) => n.props.accessibilityLabel === label && typeof n.props.onPress === 'function')[0].props.onPress());

beforeEach(() => {
  mockPush.mockClear(); mockUpdate.mockClear(); mockPutBack.mockClear(); mockUnstar.mockClear();
  mockSettings = DEFAULTS;
  mockList = { cards: [], isPending: false, isError: false, data: {}, isRefetching: false, refetch: jest.fn() };
});

describe('History', () => {
  test('empty: explains what will show up here', () => {
    const t = words(render(<History />));
    expect(t).toContain('History');
    expect(t).toContain('Nothing here yet');
  });
  test('loading and failing states render', () => {
    mockList = { ...mockList, isPending: true, data: undefined };
    expect(() => render(<History />)).not.toThrow();
    mockList = { ...mockList, isPending: false, isError: true, data: undefined };
    const t = words(render(<History />));
    expect(t).toContain('Can’t load your history');
    expect(t).not.toContain('Nothing here yet');
  });
  test('cards are listed with their kind of swipe, open on tap, and "Put back" does its job', () => {
    const a = messageCard({ id: 11, swiped: { state: 'dismissed', at: '2026-10-26T09:00:00+05:30' } });
    const b = documentCard({ id: 12, swiped: { state: 'opened', at: '2026-10-26T08:00:00+05:30' } });
    mockList = { ...mockList, cards: [a, b], data: { cards: [a, b] } };
    const r = render(<History />);
    const t = words(r);
    expect(t).toContain('Quiz 1 is on Monday');
    expect(t).toContain('Syllabus v2.pdf');
    expect(t).toContain('Dismissed');
    expect(t).toContain('Opened');
    press(r, 'Put back in the deck');
    expect(mockPutBack).toHaveBeenCalledWith(a);
    press(r, 'MA 221. Syllabus v2.pdf');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/item/[id]', params: { id: '12' } });
  });
  test('the title says how many cards are kept, from the settings', () => {
    mockSettings = { ...DEFAULTS, history_limit: 120 };
    expect(words(render(<History />))).toContain('Your last 120 cards');
  });
  test('the settings gear is on the screen', () => {
    const r = render(<History />);
    press(r, 'Settings');
    expect(mockPush).toHaveBeenCalledWith('/settings');
  });
});

describe('Starred', () => {
  const NOW = Date.now();
  const star = (over = {}) => messageCard({ id: 21, user: { ...messageCard().user, starred: true }, ...over });
  test('empty: tells how to star something', () => {
    expect(words(render(<Starred />))).toContain('No stars yet');
  });
  test('lists stars, shows what is left of a temporary one, and removes a star', () => {
    const permanent = star({ id: 21 });
    const temp = star({ id: 22, user: { ...permanent.user, temp_star_expires_at: new Date(NOW + 2.5 * 86_400_000).toISOString() } });
    mockList = { ...mockList, cards: [permanent, temp], data: { cards: [permanent, temp] } };
    const r = render(<Starred />);
    const t = words(r);
    expect(t).toContain('2 saved');
    expect(t).toContain('3 days left');
    press(r, 'Remove star');
    expect(mockUnstar).toHaveBeenCalledTimes(1);
  });
  test('a temporary star the phone knows has run out is not shown', () => {
    const gone = star({ id: 23, user: { ...star().user, temp_star_expires_at: new Date(NOW - 60_000).toISOString() } });
    const kept = star({ id: 24 });
    mockList = { ...mockList, cards: [gone, kept], data: { cards: [gone, kept] } };
    const r = render(<Starred />);
    expect(words(r)).toContain('1 saved');
    expect(r.root.findAll((n) => n.props.accessibilityLabel === 'Remove star' && typeof n.props.onPress === 'function')).toHaveLength(1);
  });
  test('a change card and a document card both render', () => {
    const cards = [changeCard({ id: 31 }), documentCard({ id: 32 })];
    mockList = { ...mockList, cards, data: { cards } };
    expect(() => render(<Starred />)).not.toThrow();
  });
});

describe('Important', () => {
  test('lists all nine kinds, with the default three ticked', () => {
    const r = render(<Important />);
    const boxes = checkboxes(r);
    expect(boxes).toHaveLength(9);
    const ticked = boxes.filter((b) => b.props.accessibilityState.checked).map((b) => b.props.accessibilityLabel);
    expect(ticked).toEqual(['Quiz', 'Exam', 'Assignment']);
    expect(words(r)).toContain('Office hours');
  });
  test('ticking a kind sends the full new list in the canonical order; unticking removes it', () => {
    const r = render(<Important />);
    act(() => r.root.findAll((n) => n.props.accessibilityLabel === 'Lab' && n.props.accessibilityRole === 'checkbox')[0].props.onPress());
    expect(mockUpdate).toHaveBeenLastCalledWith({ important_types: toggleType(DEFAULTS.important_types, 'lab') });
    expect(mockUpdate).toHaveBeenLastCalledWith({ important_types: ['quiz', 'exam', 'assignment', 'lab'] });
    act(() => r.root.findAll((n) => n.props.accessibilityLabel === 'Quiz' && n.props.accessibilityRole === 'checkbox')[0].props.onPress());
    expect(mockUpdate).toHaveBeenLastCalledWith({ important_types: ['exam', 'assignment'] });
  });
  test('the automatic-reminders switch sends only that field', () => {
    const r = render(<Important />);
    const sw = r.root.findAll((n) => n.props.accessibilityLabel === 'Remind me automatically' && typeof n.props.onValueChange === 'function')[0];
    expect(sw.props.value).toBe(true);
    act(() => sw.props.onValueChange(false));
    expect(mockUpdate).toHaveBeenLastCalledWith({ reminders: { auto_for_important: false } });
  });
  test('the screen reflects the settings it is given', () => {
    mockSettings = { ...DEFAULTS, important_types: ['project'], reminders: { ...DEFAULTS.reminders, auto_for_important: false } };
    const r = render(<Important />);
    expect(checkboxes(r).filter((b) => b.props.accessibilityState.checked).map((b) => b.props.accessibilityLabel)).toEqual(['Project']);
    expect(r.root.findAll((n) => n.props.accessibilityLabel === 'Remind me automatically' && typeof n.props.onValueChange === 'function')[0].props.value).toBe(false);
  });
});

test('the event helper is exercised too (keeps the fixtures honest)', () => expect(event().important).toBe(true));
