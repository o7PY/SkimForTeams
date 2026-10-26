import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Platform } from 'react-native';

import { useAuth } from './auth';
import { queryKeys } from './query';
import { useSubscriptions } from './queries';
import { RegistrationGuard, registerDevice, registrationNotice, unregisterDevice, withTimeout, type PushDeps, type RegisterResult, type UnregisterResult } from './push-registration';

type PushApi = {
  /** Called once after the first successful join: this is the only place the permission prompt is shown. */
  registerAfterJoin(): Promise<RegisterResult | null>;
  /** The student pressed "Turn on notifications": show the prompt, register this phone, and schedule the reminders. */
  enable(): Promise<RegisterResult | null>;
  unregister(): Promise<UnregisterResult>;
  /** something the student can act on (permission off, phone linked to another account), or null */
  notice: string | null;
  dismissNotice(): void;
};

const Ctx = createContext<PushApi | null>(null);
const UNREGISTER_TIMEOUT_MS = 10_000;

// While the app is in the foreground a push shows as a banner (P4-D-121); taps are handled in T12.
Notifications.setNotificationHandler({
  // On Android, shouldPlaySound:false also suppresses the heads-up banner, so it stays true (the channel decides the actual sound).
  handleNotification: async () => ({ shouldPlaySound: true, shouldSetBadge: false, shouldShowBanner: true, shouldShowList: true }),
});

export function PushProvider({ userId, children }: { userId: string | null; children: ReactNode }) {
  const { api } = useAuth();
  const subs = useSubscriptions();
  const qc = useQueryClient();
  const hasTeams = (subs.data?.length ?? 0) > 0;
  const [notice, setNotice] = useState<string | null>(null);

  const deps = useMemo<PushDeps | null>(
    () =>
      userId
        ? {
            notifications: Notifications as unknown as PushDeps['notifications'],
            api, store: AsyncStorage, userId,
            platform: Platform.OS === 'ios' ? 'ios' : 'android',
            isDevice: Device.isDevice,
            projectId: (Constants.expoConfig?.extra?.eas?.projectId as string | undefined) ?? Constants.easConfig?.projectId,
          }
        : null,
    [api, userId],
  );

  // What the student should be told about a registration attempt (nothing for the normal outcomes).
  const report = useCallback((r: RegisterResult): RegisterResult => {
    const n = registrationNotice(r);
    if (n) setNotice(n);
    return r;
  }, []);

  // One guard per account: it stops the register -> token event -> register loop and goes quiet once sign-out begins.
  const guard = useMemo(() => new RegistrationGuard(), [userId]); // eslint-disable-line react-hooks/exhaustive-deps

  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attempt = useCallback(
    async (reason: 'start' | 'token' | 'ask', nativeToken?: string): Promise<RegisterResult | null> => {
      if (!deps || !guard.tryStart(reason, nativeToken)) return null;
      try {
        const r = await registerDevice(deps, { ask: reason === 'ask' });
        if (__DEV__) console.log('[skim] registration (%s):', reason, r.status, 'reason' in r ? r.reason : '');
        return report(r);
      } finally {
        guard.done();
        const waiting = guard.consumePending(); // a real token change arrived meanwhile: handle it once the gap is over
        if (waiting && !retryTimer.current) {
          retryTimer.current = setTimeout(() => {
            retryTimer.current = null;
            void attempt('token', waiting);
          }, guard.gapMs + 500);
        }
      }
    },
    [deps, guard, report],
  );

  // Signed in and in at least one team: quietly keep this phone registered (no prompt) and follow REAL token changes.
  useEffect(() => {
    if (!deps || !hasTeams) return;
    void attempt('start');
    const sub = Notifications.addPushTokenListener((t) => void attempt('token', typeof t?.data === 'string' ? t.data : undefined));
    return () => {
      sub.remove();
      if (retryTimer.current) clearTimeout(retryTimer.current);
      retryTimer.current = null;
    };
  }, [deps, hasTeams, attempt]);

  const value = useMemo<PushApi>(
    () => ({
      registerAfterJoin: () => attempt('ask'),
      enable: async () => {
        const r = await attempt('ask');
        // Reminders were skipped while permission was missing: refetch them so they are scheduled now.
        if (r?.status === 'registered' || r?.status === 'offline') void qc.invalidateQueries({ queryKey: queryKeys.reminders });
        return r;
      },
      unregister: async () => {
        if (!deps) return 'none';
        guard.suspend(); // from here on nothing may register this phone again
        const r = await withTimeout<UnregisterResult>(unregisterDevice(deps), UNREGISTER_TIMEOUT_MS, 'offline');
        if (__DEV__) console.log('[skim] sign-out unregister:', r); // result only, never the token
        return r;
      },
      notice,
      dismissNotice: () => setNotice(null),
    }),
    [attempt, deps, guard, notice, qc],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePush(): PushApi {
  const v = useContext(Ctx);
  if (!v) throw new Error('usePush must be used inside <PushProvider>');
  return v;
}
