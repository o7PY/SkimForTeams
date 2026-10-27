// "I have to go in the app and refresh it myself" / "it gets disconnected and never catches up": on a phone, opening the app and the
// connection coming back must refresh the screens. Also: the offline banner must not claim "offline" while there is a connection.
import { focusManager, onlineManager } from '@tanstack/react-query';
import { AppState } from 'react-native';
import { act, create } from 'react-test-renderer';

import { calm } from '@/lib/lists';
import { wireQueryLifecycle } from '@/lib/query-lifecycle';
import { useOnline } from '@/lib/use-online';

jest.mock('@react-native-async-storage/async-storage', () => jest.requireActual('@react-native-async-storage/async-storage/jest/async-storage-mock'));
jest.mock('@/lib/auth', () => ({ useAuth: () => ({ api: {} }) }));
jest.mock('@/lib/outbox-runtime', () => ({ useOutbox: () => ({ ops: [], pending: 0, enqueue: jest.fn() }) }));

let netListener: ((s: { isConnected?: boolean; isInternetReachable?: boolean }) => void) | null = null;
let appListener: ((s: string) => void) | null = null;
let mockNet: { isConnected?: boolean; isInternetReachable?: boolean } = { isConnected: true, isInternetReachable: true };
jest.mock('expo-network', () => ({
  useNetworkState: () => mockNet,
  addNetworkStateListener: (cb: typeof netListener) => {
    netListener = cb;
    return { remove: () => { netListener = null; } };
  },
}));

beforeAll(() => {
  jest.spyOn(AppState, 'addEventListener').mockImplementation(((_: string, cb: (s: string) => void) => {
    appListener = cb;
    return { remove: () => { appListener = null; } };
  }) as unknown as typeof AppState.addEventListener);
  wireQueryLifecycle();
  wireQueryLifecycle(); // calling it twice must not double-wire
  // A query client keeps a permanent subscription, which is what keeps the listeners alive (TanStack wires them lazily and drops
  // them when the last subscriber leaves): do the same here.
  focusManager.subscribe(() => {});
  onlineManager.subscribe(() => {});
});

describe('opening the app refreshes it', () => {
  test('the app coming to the foreground is "focus", going to the background is not', () => {
    const seen: boolean[] = [];
    const off = focusManager.subscribe((f) => seen.push(f));
    act(() => appListener!('background'));
    act(() => appListener!('active'));
    off();
    expect(seen).toEqual([false, true]);
    expect(focusManager.isFocused()).toBe(true);
  });
});

describe('the connection coming back refreshes it', () => {
  test('connected is online, no connection is offline, and back again', () => {
    const seen: boolean[] = [];
    const off = onlineManager.subscribe((o) => seen.push(o));
    act(() => netListener!({ isConnected: false }));
    expect(onlineManager.isOnline()).toBe(false);
    act(() => netListener!({ isConnected: true, isInternetReachable: false }));      // a network that fails its own internet check is still online
    expect(onlineManager.isOnline()).toBe(true);
    act(() => netListener!({ isConnected: true }));
    off();
    expect(seen).toEqual([false, true]);
  });
  test('an unknown state counts as online (never block the app on a guess)', () => {
    act(() => netListener!({ isConnected: false }));
    act(() => netListener!({}));
    expect(onlineManager.isOnline()).toBe(true);
  });
});

describe('the offline banner and the chat input', () => {
  const online = () => {
    let out: boolean | undefined;
    function Probe() {
      out = useOnline();
      return null;
    }
    act(() => { create(<Probe />); });
    return out;
  };
  test('offline only when there is no connection', () => {
    mockNet = { isConnected: false };
    expect(online()).toBe(false);
  });
  test('"internet not reachable" on a connected network (a blocked connectivity check) is NOT offline', () => {
    mockNet = { isConnected: true, isInternetReachable: false };
    expect(online()).toBe(true);
    mockNet = { isConnected: true, isInternetReachable: undefined };
    expect(online()).toBe(true);
    mockNet = {};
    expect(online()).toBe(true);
  });
});

describe('lists that wait for the outbox', () => {
  test('hold still, also on foreground, while a change is waiting to be sent; refresh freely otherwise', () => {
    expect(calm(0)).toEqual({});
    expect(calm(2)).toEqual({ refetchOnMount: false, refetchOnReconnect: false, refetchOnWindowFocus: false });
  });
});
