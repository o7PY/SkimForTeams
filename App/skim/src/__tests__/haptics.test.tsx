// Haptics fire only where P5-D-108 says: a selection tick on checkbox and toggle (the star and the swipe are covered elsewhere).
import * as Haptics from 'expo-haptics';
import { act, create } from 'react-test-renderer';
import { haptics } from '@/lib/haptics';
import { CheckRow, SwitchRow } from '@/ui/rows';

// The icon font is a binary asset Jest cannot resolve: a stub that prints the icon's name.
jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});

const press = (r: ReturnType<typeof create>, role: string) => {
  const node = r.root.findAll((n) => n.props.accessibilityRole === role && typeof n.props.onPress === 'function')[0];
  act(() => node.props.onPress());
};

beforeEach(() => jest.clearAllMocks());

test('ticking a checkbox gives a selection tick and still reports the change', () => {
  const onChange = jest.fn();
  let r!: ReturnType<typeof create>;
  act(() => { r = create(<CheckRow label="Quiz" type="quiz" checked={false} onChange={onChange} />); });
  press(r, 'checkbox');
  expect(Haptics.selectionAsync).toHaveBeenCalledTimes(1);
  expect(onChange).toHaveBeenCalledWith(true);
  expect(Haptics.impactAsync).not.toHaveBeenCalled();
});

test('flipping a toggle gives a selection tick and still reports the change', () => {
  const onChange = jest.fn();
  let r!: ReturnType<typeof create>;
  act(() => { r = create(<SwitchRow label="Mute" value={false} onChange={onChange} />); });
  const sw = r.root.findAll((n) => typeof n.props.onValueChange === 'function')[0];
  act(() => sw.props.onValueChange(true));
  expect(Haptics.selectionAsync).toHaveBeenCalledTimes(1);
  expect(onChange).toHaveBeenCalledWith(true);
});

test('a haptic that fails never throws into the action', async () => {
  (Haptics.selectionAsync as jest.Mock).mockRejectedValueOnce(new Error('no motor'));
  expect(() => haptics.selection()).not.toThrow();
  await Promise.resolve();
});
