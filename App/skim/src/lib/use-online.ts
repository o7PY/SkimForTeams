// Presentation only: is the phone online right now? Drives the offline banner and the chat input; nothing else reads it.
// Offline means NO connection (the rule the outbox uses). It deliberately ignores `isInternetReachable`: on a network whose own
// connectivity test is blocked or slow (a campus Wi-Fi, a captive portal) Android reports "not reachable" while our server answers
// fine, which showed a false "You're offline" banner and blocked the chat. A request that truly fails reports its own error.
import * as Network from 'expo-network';

export function useOnline(): boolean {
  return Network.useNetworkState().isConnected !== false;
}
