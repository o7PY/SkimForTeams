// Presentation checks (P5-D-109, section 4.3): designed states, 48 dp touch targets, text that scales to 130%.
import { act, create } from 'react-test-renderer';

import { AppText, MAX_FONT_SCALE } from '@/ui/app-text';
import { IconButton, MIN_TARGET, TextButton } from '@/ui/buttons';
import { CaughtUpArt, DeckSkeleton, ErrorState, OfflineBanner, RowSkeletons } from '@/ui/states';

jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});
const mockOnline = jest.fn(() => true);
jest.mock('@/lib/use-online', () => ({ useOnline: () => mockOnline() }));

const render = (el: React.ReactElement) => {
  let r!: ReturnType<typeof create>;
  act(() => { r = create(el); });
  return r;
};
const texts = (r: ReturnType<typeof create>) => r.root.findAll((n) => (n.type as unknown) === 'Text').map((n) => n.children.join('')).join(' | ');
const slop = (h: unknown) => (typeof h === 'number' ? { top: h, bottom: h, left: h, right: h } : (h as { top: number; bottom: number; left: number; right: number }));

test.each([36, 40, 44, 48, 56])('an icon button drawn %i dp wide has a touch area of at least 48 dp', (size) => {
  const r = render(<IconButton icon="close" label="Close" size={size} onPress={jest.fn()} />);
  const p = r.root.findAll((n) => n.props.accessibilityRole === 'button' && n.props.hitSlop !== undefined)[0];
  const h = slop(p.props.hitSlop);
  expect(size + h.left + h.right).toBeGreaterThanOrEqual(MIN_TARGET);
  expect(size + h.top + h.bottom).toBeGreaterThanOrEqual(MIN_TARGET);
});

test('a text button is at least 48 dp tall to touch', () => {
  const r = render(<TextButton label="Undo" onPress={jest.fn()} />);
  const p = r.root.findAll((n) => n.props.accessibilityRole === 'button' && n.props.hitSlop !== undefined)[0];
  expect(20 + slop(p.props.hitSlop).top + slop(p.props.hitSlop).bottom).toBeGreaterThanOrEqual(MIN_TARGET);
});

test('text follows the phone up to 130% and no further', () => {
  const r = render(<AppText>hello</AppText>);
  expect(MAX_FONT_SCALE).toBe(1.3);
  expect(r.root.findAll((n) => n.props.maxFontSizeMultiplier === 1.3).length).toBeGreaterThan(0);
});

test('an error state says what failed and its retry does the retry', () => {
  const retry = jest.fn();
  const r = render(<ErrorState title="Can’t load" text="Check your connection." onRetry={retry} />);
  expect(texts(r)).toContain('Can’t load');
  act(() => r.root.findAll((n) => n.props.accessibilityLabel === 'Try again' && typeof n.props.onPress === 'function')[0].props.onPress());
  expect(retry).toHaveBeenCalledTimes(1);
});

test('loading skeletons announce themselves; the caught-up picture is hidden from screen readers', () => {
  expect(render(<DeckSkeleton />).root.findAll((n) => n.props.accessibilityRole === 'progressbar').length).toBeGreaterThan(0);
  expect(render(<RowSkeletons count={3} />).root.findAll((n) => n.props.accessibilityRole === 'progressbar').length).toBeGreaterThan(0);
  expect(render(<CaughtUpArt />).root.findAll((n) => n.props.importantForAccessibility === 'no-hide-descendants').length).toBeGreaterThan(0);
});

test('the offline banner shows only when the phone is offline', () => {
  mockOnline.mockReturnValue(true);
  expect(render(<OfflineBanner />).toJSON()).toBeNull();
  mockOnline.mockReturnValue(false);
  expect(texts(render(<OfflineBanner />))).toContain('You’re offline');
});
