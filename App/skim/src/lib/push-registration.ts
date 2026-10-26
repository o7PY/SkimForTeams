// Registering this phone for change alerts (PHASE4.md section 7.3, P4-D-121, P4-D-161). The notification module is injected so the
// rules can be tested: permission is asked for only when told to (after the first successful join), channels exist before the
// Android 13 prompt, a token another account holds is reported (409), and sign-out unregisters first.
import { NOTIFICATION_COLOR } from '../constants/theme';
import { ApiError, type Api } from './api';
import type { KeyValue } from './outbox';

export type NotificationsLike = {
  AndroidImportance: { HIGH: number };
  setNotificationChannelAsync(id: string, channel: { name: string; importance: number; description?: string; lightColor?: string }): Promise<unknown>;
  getPermissionsAsync(): Promise<{ granted: boolean; canAskAgain: boolean }>;
  requestPermissionsAsync(): Promise<{ granted: boolean }>;
  getExpoPushTokenAsync(o: { projectId: string }): Promise<{ data: string }>;
};

export type PushDeps = {
  notifications: NotificationsLike;
  api: Pick<Api, 'post' | 'delete'>;
  store: KeyValue;
  userId: string;
  platform: 'android' | 'ios';
  isDevice: boolean;
  projectId: string | undefined;
};

export type RegisterResult =
  | { status: 'registered'; token: string }
  | { status: 'needs_permission' }
  | { status: 'denied'; canAskAgain: boolean }
  | { status: 'taken' }
  | { status: 'too_many_devices' }
  | { status: 'offline'; token: string }
  | { status: 'unavailable'; reason: 'not_a_device' | 'no_project_id' | 'token_failed' | 'rejected' };

export const CHANNELS = [
  { id: 'reminders', name: 'Reminders', description: 'Reminders you set, and the ones Skim sets for quizzes, exams and assignments' },
  { id: 'changes', name: 'Changes', description: 'When a date, time or place changes for something you follow' },
] as const;

export const tokenKey = (userId: string) => `skim-push-token:${userId}`;

export async function registerDevice(d: PushDeps, opts: { ask: boolean }): Promise<RegisterResult> {
  if (!d.isDevice) return { status: 'unavailable', reason: 'not_a_device' };
  if (!d.projectId) return { status: 'unavailable', reason: 'no_project_id' };
  // The Android 13 permission prompt only appears once a channel exists, and tokens need the channels anyway.
  for (const c of CHANNELS) {
    await d.notifications.setNotificationChannelAsync(c.id, {
      name: c.name, description: c.description, importance: d.notifications.AndroidImportance.HIGH, lightColor: NOTIFICATION_COLOR,
    });
  }
  let perm = await d.notifications.getPermissionsAsync();
  if (!perm.granted) {
    if (!opts.ask) return { status: 'needs_permission' };
    if (!perm.canAskAgain) return { status: 'denied', canAskAgain: false };
    perm = { ...perm, ...(await d.notifications.requestPermissionsAsync()) };
    if (!perm.granted) return { status: 'denied', canAskAgain: (await d.notifications.getPermissionsAsync()).canAskAgain };
  }
  let token: string;
  try {
    token = (await d.notifications.getExpoPushTokenAsync({ projectId: d.projectId })).data;
  } catch {
    return { status: 'unavailable', reason: 'token_failed' }; // offline, or FCM is not set up for this build
  }
  try {
    await d.api.post('/devices', { expo_push_token: token, platform: d.platform });
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.isNetwork) return { status: 'offline', token };
      if (e.status === 409) return { status: 'taken' };
      if (e.status === 429) return { status: 'too_many_devices' };
    }
    return { status: 'unavailable', reason: 'rejected' };
  }
  await d.store.setItem(tokenKey(d.userId), token).catch(() => undefined);
  return { status: 'registered', token };
}

export type UnregisterResult = 'ok' | 'none' | 'offline' | 'error';

/** Sign-out step 1: tell the server this phone no longer belongs to this account (P4-D-161).
 *  Two sources for the token, so a lost local copy cannot leave the phone linked: the one remembered at registration AND the one
 *  the OS reports now. "none" is returned only when there is truly no token to remove. */
export async function unregisterDevice(d: PushDeps): Promise<UnregisterResult> {
  const stored = await d.store.getItem(tokenKey(d.userId)).catch(() => null);
  const tokens = new Set<string>(stored ? [stored] : []);
  if (d.isDevice && d.projectId) {
    try {
      if ((await d.notifications.getPermissionsAsync()).granted) tokens.add((await d.notifications.getExpoPushTokenAsync({ projectId: d.projectId })).data);
    } catch {
      // offline or FCM unavailable: the remembered token (if any) is still removed below
    }
  }
  if (tokens.size === 0) return 'none';
  let result: UnregisterResult = 'ok';
  for (const token of tokens) {
    try {
      await d.api.delete(`/devices/${encodeURIComponent(token)}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) continue; // the server does not hold this one (not registered under this account)
      result = e instanceof ApiError && e.isNetwork ? 'offline' : 'error';
      continue; // keep going: removing the other token still helps; the remembered copy stays for a retry
    }
    if (token === stored) await d.store.removeItem(tokenKey(d.userId)).catch(() => undefined);
  }
  if (result === 'ok' && stored) await d.store.removeItem(tokenKey(d.userId)).catch(() => undefined);
  return result;
}

/** The sentence to show for a registration that did not complete, or null when there is nothing to tell the student. */
export function registrationNotice(r: RegisterResult): string | null {
  switch (r.status) {
    case 'denied':
      return r.canAskAgain ? null : 'Notifications are turned off for Skim. You can turn them on in your phone settings to get alerts when dates change.';
    case 'taken':
      return 'This phone is still linked to another Skim account. Sign out there first (while online), then alerts can start here.';
    case 'too_many_devices':
      return 'This account already has 10 phones set up for alerts. Sign out of one of them first.';
    default:
      return null;
  }
}

/** Decides whether a registration attempt may start. Without this, asking the OS for a token fires the "token changed" listener,
 *  the listener registers again, which asks for the token again... forever (seen on a real phone). The rules: one at a time; a
 *  quiet attempt at most once per `startGapMs`; a token event only when the NATIVE token really differs from the last one seen, and
 *  never more often than `tokenGapMs`; nothing at all once sign-out has begun; a deliberate ("ask") attempt always goes through
 *  unless sign-out has begun. */
export class RegistrationGuard {
  private inflight = false;
  private suspended = false;
  private lastRun = -Infinity;
  private lastNativeToken: string | null = null;
  private pending: string | null = null;

  constructor(private opts: { startGapMs?: number; tokenGapMs?: number; now?: () => number } = {}) {}

  private now = () => (this.opts.now ?? Date.now)();

  tryStart(reason: 'start' | 'token' | 'ask', nativeToken?: string): boolean {
    if (this.suspended) return false;
    const t = this.now();
    if (reason !== 'ask') {
      if (reason === 'token' && nativeToken !== undefined && nativeToken === this.lastNativeToken) return false; // our own echo, not news
      if (this.inflight || (reason === 'start' && t - this.lastRun < (this.opts.startGapMs ?? 60_000)) ||
          (reason === 'token' && t - this.lastRun < (this.opts.tokenGapMs ?? 10_000))) {
        // A REAL token change that arrives while busy or too soon must not be lost: keep it to retry once the gap is over.
        if (reason === 'token' && nativeToken !== undefined) this.pending = nativeToken;
        return false;
      }
    }
    if (reason === 'token' && nativeToken !== undefined) this.lastNativeToken = nativeToken;
    if (reason !== 'ask') this.pending = null;
    this.inflight = true;
    this.lastRun = t;
    return true;
  }

  done(): void {
    this.inflight = false;
  }

  /** A token change that had to wait: returns it once (then forgets it) so the caller can retry after the gap. */
  consumePending(): string | null {
    const p = this.pending;
    this.pending = null;
    return p && p !== this.lastNativeToken ? p : null;
  }

  /** Sign-out has begun: no registration may run again for this account. */
  suspend(): void {
    this.suspended = true;
    this.pending = null;
  }

  get gapMs(): number {
    return this.opts.tokenGapMs ?? 10_000;
  }
}

/** Resolve with `fallback` if `p` has not settled within `ms` (sign-out must never hang on a jammed network). */
export function withTimeout<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  return new Promise<T>((resolve) => {
    const timer = setTimeout(() => resolve(fallback), ms);
    p.then(
      (v) => { clearTimeout(timer); resolve(v); },
      () => { clearTimeout(timer); resolve(fallback); },
    );
  });
}
