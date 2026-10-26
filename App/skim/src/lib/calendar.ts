// "Add to calendar" (P4-D-130, spike S4). First choice: expo-calendar's system "new event" form. If that cannot be reached (no permission,
// no writable calendar, the module errors) fall back to Android's own insert-event screen, which needs no permission. Which one ran is
// reported, so the S4 result can be read off the phone.
import { calendarIntentExtras, type CalendarEventDraft } from './actions';

export type CalendarOutcome = { via: 'expo-calendar' | 'intent'; opened: true } | { via: 'none'; opened: false; reason: string };

export type CalendarDeps = {
  /** open the form through expo-calendar; resolve when it has been shown. Throws if it cannot. */
  viaExpoCalendar(d: CalendarEventDraft): Promise<void>;
  /** open Android's insert-event screen. Throws if it cannot. */
  viaIntent(d: CalendarEventDraft): Promise<void>;
};

export async function addToCalendarWith(deps: CalendarDeps, d: CalendarEventDraft): Promise<CalendarOutcome> {
  try {
    await deps.viaExpoCalendar(d);
    return { via: 'expo-calendar', opened: true };
  } catch (first) {
    try {
      await deps.viaIntent(d);
      return { via: 'intent', opened: true };
    } catch (second) {
      return { via: 'none', opened: false, reason: `${errName(first)} / ${errName(second)}` };
    }
  }
}
const errName = (e: unknown) => (e instanceof Error ? e.name || 'Error' : 'Error');

/** The real thing, wired to the native modules. */
export async function realCalendarDeps(): Promise<CalendarDeps> {
  const Calendar = await import('expo-calendar');
  const IntentLauncher = await import('expo-intent-launcher');
  return {
    async viaExpoCalendar(d) {
      const perm = await Calendar.requestCalendarPermissions(true);
      if (!perm.granted) throw new Error('calendar permission was not granted');
      const calendars = await Calendar.getCalendars();
      const target = calendars.find((c) => c.allowsModifications && c.isPrimary) ?? calendars.find((c) => c.allowsModifications);
      if (!target) throw new Error('no writable calendar');
      await target.addEventWithForm({ title: d.title, startDate: d.startDate, endDate: d.endDate, allDay: d.allDay, location: d.location, notes: d.notes });
    },
    async viaIntent(d) {
      await IntentLauncher.startActivityAsync('android.intent.action.INSERT', { data: 'content://com.android.calendar/events', extra: calendarIntentExtras(d) });
    },
  };
}
