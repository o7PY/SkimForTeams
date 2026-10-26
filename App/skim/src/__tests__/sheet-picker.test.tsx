import { act, create, type ReactTestInstance } from 'react-test-renderer';

import { stepHour, stepMinute, TimePicker, type PickerValue } from '@/ui/time-picker';
import { OptionRow, Sheet } from '@/ui/sheet';

jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});
jest.mock('react-native-safe-area-context', () => {
  const { View } = jest.requireActual('react-native');
  return { SafeAreaView: View };
});

const v = (hh: number, mm: number, dayOffset = 0): PickerValue => ({ dayOffset, hh, mm });

describe('picker stepping', () => {
  test('hours wrap around the clock without changing the day or minutes', () => {
    expect(stepHour(v(23, 30), 1)).toEqual(v(0, 30));
    expect(stepHour(v(0, 30), -1)).toEqual(v(23, 30));
    expect(stepHour(v(9, 5, 3), 1)).toEqual(v(10, 5, 3));
  });
  test('minutes move in 5s, wrap at the hour (without touching the hour), and snap odd values to a multiple of 5', () => {
    expect(stepMinute(v(9, 55), 1)).toEqual(v(9, 0));
    expect(stepMinute(v(9, 0), -1)).toEqual(v(9, 55));
    expect(stepMinute(v(9, 30), 1)).toEqual(v(9, 35));
    expect(stepMinute(v(9, 32), 1)).toEqual(v(9, 35)); // 32 + 5 = 37 -> 35
    expect(stepMinute(v(9, 32), -1)).toEqual(v(9, 25)); // 32 - 5 = 27 -> 25
  });
  test('repeated stepping covers every 5-minute slot exactly once', () => {
    let x = v(0, 0);
    const seen = new Set<number>();
    for (let i = 0; i < 12; i++) { seen.add(x.mm); x = stepMinute(x, 1); }
    expect(seen.size).toBe(12);
    expect(x).toEqual(v(0, 0));
  });
});

const flat = (style: unknown): Record<string, unknown> => Object.assign({}, ...[style].flat(5).filter(Boolean));
const OPAQUE = /^#[0-9A-Fa-f]{6}$/;
const views = (r: ReturnType<typeof create>) => r.root.findAll((n) => (n.type as unknown) === 'View');
const isText = (n: ReactTestInstance) => (n.type as unknown) === 'Text';

test('the sheet panel and the option rows are solid colours', () => {
  let r!: ReturnType<typeof create>;
  act(() => {
    r = create(
      <Sheet visible title="Snooze until" onClose={() => undefined}>
        <OptionRow icon="clock-outline" label="1 hour" detail="Mon 26 Oct, 13:00" onPress={() => undefined} />
        <OptionRow label="Selected" onPress={() => undefined} selected />
      </Sheet>,
    );
  });
  const panel = r.root.findAll((n) => (n.type as unknown) === 'View' && flat(n.props.style).borderTopLeftRadius !== undefined)[0];
  expect(flat(panel.props.style).backgroundColor).toMatch(OPAQUE);
  const rows = r.root.findAll((n) => n.props.accessibilityRole === 'button' && typeof n.props.accessibilityLabel === 'string' && /hour|Selected/.test(n.props.accessibilityLabel) && n.props.style);
  expect(rows.length).toBeGreaterThan(0);
  for (const row of rows) {
    const style = flat(typeof row.props.style === 'function' ? row.props.style({ pressed: false }) : row.props.style);
    expect(style.backgroundColor).toMatch(OPAQUE);
  }
});

test('an option row reads as one button: label then detail', () => {
  let r!: ReturnType<typeof create>;
  const onPress = jest.fn();
  act(() => { r = create(<OptionRow label="Tomorrow" detail="Tue 27 Oct, 09:00" onPress={onPress} />); });
  const btn = r.root.findAll((n) => n.props.accessibilityRole === 'button')[0];
  expect(btn.props.accessibilityLabel).toBe('Tomorrow, Tue 27 Oct, 09:00');
  act(() => btn.props.onPress());
  expect(onPress).toHaveBeenCalledTimes(1);
});

test('a disabled option row cannot be pressed', () => {
  let r!: ReturnType<typeof create>;
  act(() => { r = create(<OptionRow label="x" onPress={() => undefined} disabled />); });
  expect(r.root.findAll((n) => n.props.accessibilityRole === 'button')[0].props.disabled).toBe(true);
});

test('the picker shows the day strip and the two steppers, and a day tap reports the new day', () => {
  const onChange = jest.fn();
  let r!: ReturnType<typeof create>;
  act(() => { r = create(<TimePicker now={new Date('2026-10-26T12:00:00+05:30')} value={v(13, 0)} onChange={onChange} />); });
  const text = r.root.findAll(isText).map((n) => n.children.join(''));
  expect(text).toEqual(expect.arrayContaining(['Today', 'Tomorrow', '13', '00']));
  const tomorrow = r.root.findAll((n) => n.props.accessibilityRole === 'button' && n.props.accessibilityState?.selected === false).find((n) => n.findAll(isText).some((t) => t.children.join('') === 'Tomorrow'))!;
  act(() => tomorrow.props.onPress());
  expect(onChange).toHaveBeenCalledWith(v(13, 0, 1));
  const up = r.root.findAll((n) => n.props.accessibilityLabel === 'Hour up')[0];
  act(() => up.props.onPress());
  expect(onChange).toHaveBeenLastCalledWith(v(14, 0));
  const down = r.root.findAll((n) => n.props.accessibilityLabel === 'Minute down')[0];
  act(() => down.props.onPress());
  expect(onChange).toHaveBeenLastCalledWith(v(13, 55));
  expect(views(r).length).toBeGreaterThan(3);
});
