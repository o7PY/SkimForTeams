// Tells TanStack Query what "the app came to the foreground" and "the phone is online again" mean on a phone. On the web it listens to
// the browser's focus and online events; in React Native nothing fires those, so without this the deck never refreshed when the
// student opened the app, and never caught up after the connection came back: they had to pull down by hand.
import { focusManager, onlineManager } from '@tanstack/react-query';
import * as Network from 'expo-network';
import { AppState } from 'react-native';

let wired = false;

/** Call once at startup. Idempotent. */
export function wireQueryLifecycle(): void {
  if (wired) return;
  wired = true;
  // Foreground = focus: queries older than their stale time refetch when the app becomes active.
  focusManager.setEventListener((handleFocus) => {
    const sub = AppState.addEventListener('change', (state) => handleFocus(state === 'active'));
    return () => sub.remove();
  });
  // Online = the phone has a connection (the same rule the outbox uses). Not "internet reachable": that check can say no on a network
  // whose own connectivity test is blocked even though our server is reachable; a request that really fails is retried anyway.
  onlineManager.setEventListener((setOnline) => {
    const sub = Network.addNetworkStateListener((state) => setOnline(state.isConnected !== false));
    return () => sub.remove();
  });
}
