import AsyncStorage from '@react-native-async-storage/async-storage';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { Alert } from 'react-native';

import { useAuth } from './auth';
import { stopBackgroundSync } from './background';
import { cancelAllReminders } from './notif-adapter';
import { outboxRef } from './outbox-runtime';
import { cacheKey } from './query';
import { usePush } from './push-runtime';
import { STATE_KEY } from './reminder-plan';

const confirmAnyway = () =>
  new Promise<boolean>((resolve) =>
    Alert.alert(
      'Can’t reach the server',
      'Alerts for this account may keep arriving on this phone until you sign in here again and sign out while online. Sign out anyway?',
      [
        { text: 'Stay signed in', style: 'cancel', onPress: () => resolve(false) },
        { text: 'Sign out anyway', style: 'destructive', onPress: () => resolve(true) },
      ],
      { cancelable: true, onDismiss: () => resolve(false) },
    ),
  );

/** Sign-out in the order P4-D-161 needs: unregister this phone while the session is still valid, then wipe what is stored on the
 *  phone for this account (cached cards, unsent changes), then end the session. Returns false if the student changed their mind. */
export function useSignOut() {
  const { session, signOut } = useAuth();
  const push = usePush();
  const qc = useQueryClient();
  const userId = session?.user.id ?? null;
  return useCallback(async (): Promise<boolean> => {
    const r = await push.unregister();
    if ((r === 'offline' || r === 'error') && !(await confirmAnyway())) return false;
    await outboxRef.current?.clear();
    await stopBackgroundSync();
    await cancelAllReminders().catch(() => undefined); // the next person on this phone must not hear this account's reminders
    if (userId) await AsyncStorage.removeItem(STATE_KEY(userId)).catch(() => undefined);
    qc.clear();
    if (userId) await AsyncStorage.removeItem(cacheKey(userId)).catch(() => undefined);
    await signOut();
    return true;
  }, [push, qc, signOut, userId]);
}
