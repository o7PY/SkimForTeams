// Presentation only: is the phone online right now? Drives the offline banner; nothing else reads it.
import * as Network from 'expo-network';

export function useOnline(): boolean {
  const s = Network.useNetworkState();
  return !(s.isConnected === false || s.isInternetReachable === false);
}
