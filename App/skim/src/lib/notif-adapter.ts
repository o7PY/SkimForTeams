// The real scheduler: expo-notifications behind the small interface lib/reminder-plan.ts uses. Thin on purpose; the rules are tested there.
import * as Notifications from 'expo-notifications';
import { AndroidImportance } from 'expo-notifications';
import { Platform } from 'react-native';

import { CHANNELS } from './push-registration';
import type { Planned, SchedulerLike } from './reminder-plan';

/** Notifications the app schedules for itself (the Settings test button). They are not the server's, so a sync must not cancel them. */
export const LOCAL_PREFIX = 'skim-local:';

let channelsReady: Promise<void> | null = null;
/** Android needs the channel to exist before a notification is posted to it. Idempotent. */
export function ensureChannels(): Promise<void> {
  if (Platform.OS !== 'android') return Promise.resolve();
  channelsReady ??= (async () => {
    for (const c of CHANNELS) {
      await Notifications.setNotificationChannelAsync(c.id, { name: c.name, description: c.description, importance: AndroidImportance.HIGH, lightColor: '#38BDF8' });
    }
  })().catch((e) => {
    channelsReady = null;
    throw e;
  });
  return channelsReady;
}

export const realScheduler: SchedulerLike = {
  async permissionGranted() {
    return (await Notifications.getPermissionsAsync()).granted;
  },
  async scheduledIds() {
    return (await Notifications.getAllScheduledNotificationsAsync()).map((n) => n.identifier).filter((id) => !id.startsWith(LOCAL_PREFIX));
  },
  async cancel(id) {
    await Notifications.cancelScheduledNotificationAsync(id);
  },
  async schedule(p: Planned) {
    await ensureChannels();
    await Notifications.scheduleNotificationAsync({
      identifier: p.id, // a re-sync with a moved time replaces the old alarm rather than adding another
      content: { title: p.title, body: p.body, data: p.data, sound: 'default' },
      trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: p.at, channelId: 'reminders' },
    });
  },
};

/** Remove every reminder this app scheduled (sign-out: the next person on this phone must not hear the last person's reminders). */
export async function cancelAllReminders(): Promise<void> {
  await Notifications.cancelAllScheduledNotificationsAsync();
}

/** A reminder 60 seconds from now: lets the student see that reminders arrive on time on this phone (acceptance A8). */
export async function scheduleTestReminder(): Promise<Date> {
  await ensureChannels();
  const at = new Date(Date.now() + 60_000);
  await Notifications.scheduleNotificationAsync({
    identifier: `${LOCAL_PREFIX}test`,
    content: { title: 'Skim test reminder', body: 'If you can read this, reminders reach this phone on time.', data: { type: 'reminder' }, sound: 'default' },
    trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: at, channelId: 'reminders' },
  });
  return at;
}

// ---------------------------------------------------------------- exact alarms (Android 12+)
export type ExactAlarm = 'granted' | 'denied' | 'unsupported';

export async function exactAlarmStatus(): Promise<ExactAlarm> {
  if (Platform.OS !== 'android') return 'unsupported';
  try {
    return (await Notifications.getExactAlarmPermissionsAsync()).granted ? 'granted' : 'denied';
  } catch {
    return 'unsupported';
  }
}

/** Opens Android's "Alarms & reminders" screen for this app. Resolves with the status after the student returns. */
export async function requestExactAlarm(): Promise<ExactAlarm> {
  try {
    return (await Notifications.requestExactAlarmPermissionsAsync()).granted ? 'granted' : 'denied';
  } catch {
    return 'unsupported'; // the permission is not declared in this build
  }
}
