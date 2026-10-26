import { act, create, type ReactTestInstance } from 'react-test-renderer';

import { BANNER_TEXT, permissionAction } from '@/lib/notif-permission';
import { NotificationsBanner } from '@/ui/notifications-banner';

describe('permissionAction', () => {
  test.each([
    [null, 'none'],                                       // not read yet: say nothing
    [{ granted: true, canAskAgain: true }, 'none'],
    [{ granted: true, canAskAgain: false }, 'none'],
    [{ granted: false, canAskAgain: true }, 'ask'],        // never asked, or refused once: the system prompt can still appear
    [{ granted: false, canAskAgain: false }, 'settings'],  // refused for good: only the phone's settings can change it
  ])('%j -> %s', (state, action) => expect(permissionAction(state)).toBe(action));

  test('each action has words, and none of them promises something the app cannot do', () => {
    expect(BANNER_TEXT.ask.button).toBe('Turn on');
    expect(BANNER_TEXT.settings.button).toBe('Open settings');
    expect(BANNER_TEXT.settings.body).toMatch(/settings/i);
  });
});

// ---- the banner: it appears only when there is something to turn on, and the button does the right thing
const mockEnable = jest.fn(async () => ({ status: 'registered' as const, token: 't' }));
const mockOpenSettings = jest.fn(async () => undefined);
let mockState: { granted: boolean; canAskAgain: boolean } | null = null;
const mockRefresh = jest.fn();

jest.mock('@/lib/use-permission', () => ({ useNotificationPermission: () => ({ state: mockState, refresh: mockRefresh }) }));
jest.mock('@/lib/push-runtime', () => ({ usePush: () => ({ enable: mockEnable }) }));
jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});
jest.mock('react-native/Libraries/Linking/Linking', () => ({ __esModule: true, default: { openSettings: () => mockOpenSettings(), addEventListener: jest.fn(), getInitialURL: jest.fn() } }));

const isText = (n: ReactTestInstance) => (n.type as unknown) === 'Text';
const render = () => {
  let r!: ReturnType<typeof create>;
  act(() => { r = create(<NotificationsBanner />); });
  return r;
};
const words = (r: ReturnType<typeof create>) => r.root.findAll(isText).map((n) => n.children.join('')).join(' | ');
const button = (r: ReturnType<typeof create>) => r.root.findAll((n) => n.props.accessibilityRole === 'button')[0];

beforeEach(() => { mockEnable.mockClear(); mockOpenSettings.mockClear(); mockRefresh.mockClear(); });

test('nothing is shown when notifications are on, or before the state is known', () => {
  mockState = { granted: true, canAskAgain: true };
  expect(render().toJSON()).toBeNull();
  mockState = null;
  expect(render().toJSON()).toBeNull();
});

test('off but askable: "Turn on notifications" and the button shows the system prompt (and registers this phone)', async () => {
  mockState = { granted: false, canAskAgain: true };
  const r = render();
  expect(words(r)).toContain('Turn on notifications');
  await act(async () => { await button(r).props.onPress(); });
  expect(mockEnable).toHaveBeenCalledTimes(1);
  expect(mockOpenSettings).not.toHaveBeenCalled();
  expect(mockRefresh).toHaveBeenCalled(); // the banner re-reads the permission and goes away if it was granted
});

test('refused for good: it says so, and the button opens the phone’s settings instead of a prompt that cannot appear', async () => {
  mockState = { granted: false, canAskAgain: false };
  const r = render();
  expect(words(r)).toContain('Notifications are off');
  await act(async () => { await button(r).props.onPress(); });
  expect(mockOpenSettings).toHaveBeenCalledTimes(1);
  expect(mockEnable).not.toHaveBeenCalled();
});
