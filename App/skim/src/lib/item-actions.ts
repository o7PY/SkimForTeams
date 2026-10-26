// Everything the action bar can do to one card. Each change is applied to the screen at once and queued in the outbox (so it works
// offline, P4-D-131); a refetch after delivery shows what the server holds. Nothing here talks to the network directly.
import { useQueryClient } from '@tanstack/react-query';
import { router } from 'expo-router';
import { Linking, Platform } from 'react-native';
import { useCallback, useMemo } from 'react';

import { calendarEventFor, fileUrl, newUuid, safeUrl, type TempStar } from './actions';
import { addToCalendarWith, realCalendarDeps, type CalendarOutcome } from './calendar';
import { asStarredEntry, dropFromList, prependToList, removeCard, stateOp, type DeckData } from './deck-cache';
import { pendingRecommended, withoutReminder, withoutStar, withReminders, withStar } from './item-cache';
import { useOutbox } from './outbox-runtime';
import { queryKeys } from './query';
import type { Card } from './types';

export type ActionResult = { ok: true; message: string } | { ok: false; message: string };
const ok = (message: string): ActionResult => ({ ok: true, message });
const fail = (message: string): ActionResult => ({ ok: false, message });

export function useItemActions(card: Card | undefined) {
  const qc = useQueryClient();
  const { enqueue } = useOutbox();
  const key = useMemo(() => [...queryKeys.item, String(card?.id)] as const, [card?.id]);

  const edit = useCallback((f: (c: Card) => Card) => qc.setQueryData<Card>(key, (c) => (c ? f(c) : c)), [qc, key]);
  const eventOf = (c: Card) => c.events.find((e) => e.status !== 'cancelled');

  return useMemo(() => {
    if (!card) return null;
    const id = card.id;
    return {
      /** the pair "day before 20:00 + same day" (P4-D-116), one tap */
      async addRecommended(): Promise<ActionResult> {
        const e = eventOf(card);
        const rid = newUuid();
        edit((c) => withReminders(c, pendingRecommended(c, rid)));
        await enqueue({ key: `reminder:${rid}`, method: 'PUT', path: `/reminders/${rid}`, body: { recommended: true, item_id: id, ...(e ? { event_id: e.id } : {}) } });
        return ok('Reminders added');
      },
      async remindBefore(minutes: number): Promise<ActionResult> {
        const e = eventOf(card);
        if (!e) return fail('This card has no event to count from');
        const rid = newUuid();
        await enqueue({ key: `reminder:${rid}`, method: 'PUT', path: `/reminders/${rid}`, body: { kind: 'manual', item_id: id, event_id: e.id, offset_minutes: -minutes } });
        void qc.invalidateQueries({ queryKey: key });
        return ok('Reminder added');
      },
      async remindAt(iso: string): Promise<ActionResult> {
        const rid = newUuid();
        await enqueue({ key: `reminder:${rid}`, method: 'PUT', path: `/reminders/${rid}`, body: { kind: 'manual', item_id: id, fire_at: iso } });
        void qc.invalidateQueries({ queryKey: key });
        return ok('Reminder added');
      },
      async removeReminder(rid: string): Promise<ActionResult> {
        edit((c) => withoutReminder(c, rid));
        await enqueue({ key: `reminder:${rid}`, method: 'DELETE', path: `/reminders/${rid}` });
        return ok('Reminder removed');
      },
      async star(temp: null | TempStar): Promise<ActionResult> {
        const starred = withStar(card, temp, new Date());
        edit(() => starred);
        // It is in the Starred tab straight away, also offline: that tab does not wait for the server.
        qc.setQueryData<{ cards: Card[] }>(queryKeys.starred, (d) => prependToList(d, asStarredEntry(starred, new Date())));
        await enqueue({ key: `star:${id}`, method: 'PUT', path: `/items/${id}/star`, body: { temp } });
        return ok(temp ? 'Temporarily starred' : 'Starred');
      },
      async unstar(): Promise<ActionResult> {
        edit(withoutStar);
        qc.setQueryData<{ cards: Card[] }>(queryKeys.starred, (d) => dropFromList(d, id));
        await enqueue({ key: `star:${id}`, method: 'DELETE', path: `/items/${id}/star` });
        return ok('Star removed');
      },
      /** leaves the deck now and comes back at the top when it ends (P4-D-115); closes the screen */
      async snooze(until: string, label: string): Promise<ActionResult> {
        qc.setQueryData<DeckData>(queryKeys.deck, (d) => removeCard(d, id));
        await enqueue(stateOp(id, 'snooze', until));
        if (router.canGoBack()) router.back();
        else router.replace('/');
        return ok(`Snoozed until ${label}`);
      },
      async addToCalendar(): Promise<{ result: ActionResult; outcome?: CalendarOutcome }> {
        const draft = calendarEventFor(card);
        if (!draft) return { result: fail('This card has no event with a date') };
        if (Platform.OS !== 'android' && Platform.OS !== 'ios') return { result: fail('Calendar is not available here') };
        const outcome = await addToCalendarWith(await realCalendarDeps(), draft);
        if (__DEV__) console.log('[skim] add to calendar:', outcome.via, 'reason' in outcome ? outcome.reason : '');
        return { outcome, result: outcome.opened ? ok('Calendar opened') : fail('Couldn’t open your calendar') };
      },
      async openTeams(): Promise<ActionResult> {
        return open(card.links.teams_deep_link, 'Couldn’t open Teams');
      },
      async openFile(): Promise<ActionResult> {
        return open(fileUrl(card), 'Couldn’t open the file');
      },
    };
  }, [card, edit, enqueue, qc, key]);
}

async function open(url: string | null | undefined, failure: string): Promise<ActionResult> {
  const safe = safeUrl(url);
  if (!safe) return fail('There is no link for this card');
  try {
    await Linking.openURL(safe);
    return ok('Opened');
  } catch {
    return fail(failure);
  }
}
