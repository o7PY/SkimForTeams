import * as Notifications from 'expo-notifications';
import { useCallback, useEffect, useState } from 'react';
import { AppState } from 'react-native';

import type { PermissionState } from './notif-permission';

/** The notification permission as it is now. Re-read whenever the app comes back to the foreground (the student may have just
 *  changed it in the phone's settings) and on demand. `null` until the first read. */
export function useNotificationPermission(): { state: PermissionState | null; refresh(): void } {
  const [state, setState] = useState<PermissionState | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    void Notifications.getPermissionsAsync()
      .then((p) => alive && setState({ granted: p.granted, canAskAgain: p.canAskAgain }))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [tick]);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => s === 'active' && setTick((n) => n + 1));
    return () => sub.remove();
  }, []);
  return { state, refresh: useCallback(() => setTick((n) => n + 1), []) };
}
