import { act, create, type ReactTestInstance } from 'react-test-renderer';

import { solid } from '@/constants/skim-theme';
import { confirmsDelete } from '@/lib/confirm';
import { CardRow, CheckRow, Empty, StepperRow, SwitchRow } from '@/ui/rows';

import { event, messageCard } from './fixtures';

jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});

const NOW = new Date('2026-10-26T12:00:00+05:30');
const isText = (n: ReactTestInstance) => (n.type as unknown) === 'Text';
const flat = (style: unknown): Record<string, unknown> => Object.assign({}, ...[style].flat(5).filter(Boolean));
const render = (el: React.ReactElement) => { let r!: ReturnType<typeof create>; act(() => { r = create(el); }); return r; };
const words = (r: ReturnType<typeof create>) => r.root.findAll(isText).map((n) => n.children.join('')).join(' | ');
const byLabel = (r: ReturnType<typeof create>, label: string) => r.root.findAll((n) => n.props.accessibilityLabel === label && typeof n.props.onPress === 'function')[0];

describe('CardRow', () => {
  const card = (over = {}) => messageCard({ occurred_at: '2026-10-25T10:00:00+05:30', ...over });
  test('shows the headline, the course and when; the action button does its one thing', () => {
    const onOpen = jest.fn();
    const onPress = jest.fn();
    const r = render(<CardRow card={card()} now={NOW} onOpen={onOpen} action={{ icon: 'undo-variant', label: 'Put back in the deck', onPress }} />);
    const t = words(r);
    expect(t).toContain('Quiz 1 is on Monday');
    expect(t).toContain('MA 221');
    expect(t).toContain('Yesterday');
    act(() => byLabel(r, 'Put back in the deck').props.onPress());
    expect(onPress).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
    act(() => byLabel(r, 'MA 221. Quiz 1 is on Monday').props.onPress());
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
  test('history rows are timed by when they were swiped, starred rows by when they were starred', () => {
    const swiped = card({ swiped: { state: 'dismissed', at: '2026-10-26T11:00:00+05:30' } });
    expect(words(render(<CardRow card={swiped} now={NOW} onOpen={jest.fn()} action={{ icon: 'undo-variant', label: 'x', onPress: jest.fn() }} />))).toContain('1 h ago');
    const starred = card({ starred_at: '2026-10-26T09:00:00+05:30' });
    expect(words(render(<CardRow card={starred} now={NOW} onOpen={jest.fn()} action={{ icon: 'star-off-outline', label: 'x', onPress: jest.fn() }} />))).toContain('3 h ago');
  });
  test('a temporary star shows how long is left; a permanent one shows nothing; the history never does', () => {
    const base = card();
    const temp = card({ user: { ...base.user, starred: true, temp_star_expires_at: '2026-10-28T12:00:00+05:30' } });
    const row = (c: typeof base, showExpiry: boolean) => words(render(<CardRow card={c} now={NOW} showExpiry={showExpiry} onOpen={jest.fn()} action={{ icon: 'star-off-outline', label: 'x', onPress: jest.fn() }} />));
    expect(row(temp, true)).toContain('2 days left');
    expect(row(temp, false)).not.toContain('left');
    expect(row({ ...base, user: { ...base.user, starred: true } }, true)).not.toContain('left');
  });
  test('an important card is marked (type in the line) and the row goes red; a deleted message says so', () => {
    const r = render(<CardRow card={card({ events: [event()] })} now={NOW} onOpen={jest.fn()} action={{ icon: 'undo-variant', label: 'x', onPress: jest.fn() }} />);
    expect(words(r)).toContain('Quiz');
    const root = r.toJSON() as unknown as { props: { style: unknown } };
    expect(flat(root.props.style).backgroundColor).toBe(solid.cardHot);
    const del = card({ message: { ...messageCard().message!, deleted: true, text: '' } });
    expect(words(render(<CardRow card={del} now={NOW} onOpen={jest.fn()} action={{ icon: 'undo-variant', label: 'x', onPress: jest.fn() }} />))).toContain('deleted in Teams');
  });
  test('the row surface is solid', () => {
    const root = render(<CardRow card={card()} now={NOW} onOpen={jest.fn()} action={{ icon: 'undo-variant', label: 'x', onPress: jest.fn() }} />).toJSON() as unknown as { props: { style: unknown } };
    expect(flat(root.props.style).backgroundColor).toMatch(/^#[0-9A-Fa-f]{6}$/);
  });
});

test('Empty explains itself', () => {
  const t = words(render(<Empty icon="history" title="Nothing here yet" text="Cards you swipe away show up here." />));
  expect(t).toContain('Nothing here yet');
  expect(t).toContain('Cards you swipe away show up here.');
});

describe('controls', () => {
  test('CheckRow reports the OPPOSITE of its current state when pressed, and announces itself as a checkbox', () => {
    const onChange = jest.fn();
    const off = render(<CheckRow label="Quiz" checked={false} onChange={onChange} />);
    const btn = off.root.findAll((n) => n.props.accessibilityRole === 'checkbox')[0];
    expect(btn.props.accessibilityState).toEqual({ checked: false });
    act(() => btn.props.onPress());
    expect(onChange).toHaveBeenLastCalledWith(true);
    const on = render(<CheckRow label="Quiz" checked onChange={onChange} />);
    expect(on.root.findAll((n) => n.props.accessibilityRole === 'checkbox')[0].props.accessibilityState).toEqual({ checked: true });
    act(() => on.root.findAll((n) => n.props.accessibilityRole === 'checkbox')[0].props.onPress());
    expect(onChange).toHaveBeenLastCalledWith(false);
    expect(words(on)).toContain('[icon:checkbox-marked]');
    expect(words(off)).toContain('[icon:checkbox-blank-outline]');
  });
  test('SwitchRow passes the new value through and can be disabled', () => {
    const onChange = jest.fn();
    const r = render(<SwitchRow label="Urgent only" detail="d" value={false} onChange={onChange} />);
    const sw = r.root.findAll((n) => n.props.accessibilityLabel === 'Urgent only' && typeof n.props.onValueChange === 'function')[0];
    act(() => sw.props.onValueChange(true));
    expect(onChange).toHaveBeenCalledWith(true);
    const off = render(<SwitchRow label="x" value onChange={onChange} disabled />);
    expect(off.root.findAll((n) => typeof n.props.onValueChange === 'function')[0].props.disabled).toBe(true);
  });
  test('StepperRow: minus and plus call their handlers and can be switched off at the ends', () => {
    const minus = jest.fn();
    const plus = jest.fn();
    const r = render(<StepperRow label="Hours before" value="5 hours" onMinus={minus} onPlus={plus} />);
    act(() => byLabel(r, 'Hours before: less').props.onPress());
    act(() => byLabel(r, 'Hours before: more').props.onPress());
    expect(minus).toHaveBeenCalledTimes(1);
    expect(plus).toHaveBeenCalledTimes(1);
    expect(words(r)).toContain('5 hours');
    const end = render(<StepperRow label="Hours before" value="30 minutes" onMinus={minus} onPlus={plus} minusDisabled />);
    expect(end.root.findAll((n) => n.props.accessibilityLabel === 'Hours before: less' && n.props.disabled !== undefined)[0].props.disabled).toBe(true);
  });
  test('control surfaces are solid', () => {
    const solidHex = /^#[0-9A-Fa-f]{6}$/;
    for (const el of [<CheckRow key="a" label="x" checked onChange={jest.fn()} />, <SwitchRow key="b" label="x" value onChange={jest.fn()} />, <StepperRow key="c" label="x" value="1" onMinus={jest.fn()} onPlus={jest.fn()} />]) {
      const bgs = render(el).root.findAll((n) => (n.type as unknown) === 'View' || n.props.accessibilityRole === 'checkbox')
        .map((n) => flat(typeof n.props.style === 'function' ? n.props.style({ pressed: false }) : n.props.style).backgroundColor).filter((c): c is string => typeof c === 'string');
      expect(bgs.length).toBeGreaterThan(0);
      for (const c of bgs) expect(c).toMatch(solidHex);
    }
  });
});

describe('deleting the account needs the word typed', () => {
  test.each([['DELETE', true], ['delete', true], ['  Delete  ', true], ['DELET', false], ['DELETE!', false], ['', false], ['yes', false], ['delete my account', false]])('%j -> %s', (typed, ok) => {
    expect(confirmsDelete(typed)).toBe(ok);
  });
});
