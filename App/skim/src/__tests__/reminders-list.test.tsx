// The Reminders list inside Starred: every reminder with an on/off switch that survives a refresh until it is delivered.
import { act, create } from 'react-test-renderer';

import type { ListedReminder } from '@/lib/reminder-plan';
import { pendingSwitches, withEnabled } from '@/lib/reminders-list';
import { reminderKindLabel, ReminderRow } from '@/ui/reminder-list';
import { Segmented } from '@/ui/segmented';

// The hooks need the signed-in session and the outbox; only the pure parts and the row are exercised here.
jest.mock('@react-native-async-storage/async-storage', () => jest.requireActual('@react-native-async-storage/async-storage/jest/async-storage-mock'));
jest.mock('@/lib/auth', () => ({ useAuth: () => ({ api: {} }) }));
jest.mock('@/lib/outbox-runtime', () => ({ useOutbox: () => ({ ops: [], pending: 0, enqueue: jest.fn() }) }));
const mockPush = jest.fn();
jest.mock('expo-router', () => ({ router: { push: (...a: unknown[]) => mockPush(...a) } }));
jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});

const ID1 = '11111111-1111-4111-8111-111111111111';
const ID2 = '22222222-2222-4222-8222-222222222222';
const rem = (over: Partial<ListedReminder> = {}): ListedReminder => ({
  id: ID1, kind: 'recommended_day_before', item_id: 7, event_id: 3, fire_at: '2026-11-01T20:00:00+05:30', title: 'Quiz 1', body: 'Tomorrow', virtual: false, enabled: true, ...over,
});
const render = (el: React.ReactElement) => { let r!: ReturnType<typeof create>; act(() => { r = create(el); }); return r; };
const words = (r: ReturnType<typeof create>) => r.root.findAll((n) => (n.type as unknown) === 'Text').map((n) => n.children.join('')).join(' | ');

beforeEach(() => jest.clearAllMocks());

describe('the pure parts', () => {
  test('withEnabled switches only the named reminder and leaves the rest alone', () => {
    const data = { reminders: [rem({ id: ID1 }), rem({ id: ID2 })] };
    const out = withEnabled(data, ID2, false)!;
    expect(out.reminders.map((r) => r.enabled)).toEqual([true, false]);
    expect(data.reminders[1].enabled).toBe(true); // not mutated
    expect(withEnabled(undefined, ID1, false)).toBeUndefined();
  });
  test('pendingSwitches reads the newest switch (or delete) the outbox still holds, per reminder', () => {
    const ops = [
      { method: 'PATCH', path: `/reminders/${ID1}`, body: { enabled: false } },
      { method: 'PATCH', path: `/reminders/${ID1}`, body: { enabled: true } },
      { method: 'DELETE', path: `/reminders/${ID2}` },
      { method: 'PUT', path: `/items/5/star`, body: {} },
      { method: 'PATCH', path: `/reminders/${ID2}x`, body: { enabled: true } },
      { method: 'PATCH', path: `/me/settings`, body: { enabled: true } },
    ];
    expect([...pendingSwitches(ops)]).toEqual([[ID1, true], [ID2, false]]);
    expect(pendingSwitches([]).size).toBe(0);
  });
  test('kinds are called Automatic, Recommended or Custom', () => {
    expect(reminderKindLabel({ kind: 'auto_same_day', virtual: true })).toBe('Automatic');
    expect(reminderKindLabel({ kind: 'auto_day_before', virtual: false })).toBe('Automatic');
    expect(reminderKindLabel({ kind: 'recommended_same_day', virtual: false })).toBe('Recommended');
    expect(reminderKindLabel({ kind: 'manual', virtual: false })).toBe('Custom');
  });
});

describe('a reminder row', () => {
  test('shows its title, when, and kind, a bell, and a switch that reports the change', () => {
    const onSwitch = jest.fn();
    const r = render(<ReminderRow reminder={rem()} onSwitch={onSwitch} />);
    const t = words(r);
    expect(t).toContain('Quiz 1');
    expect(t).toContain('Recommended');
    expect(t).toContain('20:00');
    expect(t).toContain('[icon:bell-ring-outline]');
    const sw = r.root.findAll((n) => typeof n.props.onValueChange === 'function')[0];
    expect(sw.props.value).toBe(true);
    act(() => sw.props.onValueChange(false));
    expect(onSwitch).toHaveBeenCalledWith(false);
  });
  test('a switched-off reminder stays visible, says Off, has a crossed bell and a switch that turns it back on', () => {
    const onSwitch = jest.fn();
    const r = render(<ReminderRow reminder={rem({ enabled: false })} onSwitch={onSwitch} />);
    expect(words(r)).toContain('Off');
    expect(words(r)).toContain('[icon:bell-off-outline]');
    const sw = r.root.findAll((n) => typeof n.props.onValueChange === 'function')[0];
    expect(sw.props.value).toBe(false);
    act(() => sw.props.onValueChange(true));
    expect(onSwitch).toHaveBeenCalledWith(true);
  });
  test('tapping the text opens the card, and every control has a label', () => {
    const onOpen = jest.fn();
    const r = render(<ReminderRow reminder={rem()} onSwitch={jest.fn()} onOpen={onOpen} />);
    const open = r.root.findAll((n) => typeof n.props.accessibilityLabel === 'string' && n.props.accessibilityLabel.endsWith('Open the card') && typeof n.props.onPress === 'function')[0];
    act(() => open.props.onPress());
    expect(onOpen).toHaveBeenCalledTimes(1);
    const sw = r.root.findAll((n) => typeof n.props.onValueChange === 'function')[0];
    expect(sw.props.accessibilityLabel).toContain('Quiz 1');
  });
});

describe('the Starred / Reminders switch', () => {
  test('marks the current view and reports a tap on the other', () => {
    const onChange = jest.fn();
    const r = render(<Segmented value="stars" onChange={onChange} options={[{ id: 'stars', label: 'Starred' }, { id: 'reminders', label: 'Reminders' }]} />);
    const tabs = r.root.findAll((n) => n.props.accessibilityRole === 'tab' && typeof n.props.onPress === 'function');
    expect(tabs.map((n) => n.props.accessibilityState.selected)).toEqual([true, false]);
    act(() => tabs[1].props.onPress());
    expect(onChange).toHaveBeenCalledWith('reminders');
  });
});
