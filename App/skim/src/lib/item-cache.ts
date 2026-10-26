// Optimistic edits of an opened card (the ['item', id] query): what the screen shows the moment a button is pressed, before the outbox
// has reached the server. A refetch after delivery replaces all of it with the server's answer.
import type { Card } from './types';

const DAY = 86_400_000;
const days = { '1d': 1, '3d': 3, '7d': 7 } as const;

export function withStar(card: Card, temp: null | '1d' | '3d' | '7d' | 'event_over', now: Date): Card {
  const expires = temp && temp !== 'event_over' ? new Date(now.getTime() + days[temp] * DAY).toISOString() : null;
  return { ...card, user: { ...card.user, starred: true, temp_star_expires_at: expires } };
}

export function withoutStar(card: Card): Card {
  return { ...card, user: { ...card.user, starred: false, temp_star_expires_at: null } };
}

export type ShownReminder = Card['user']['reminders'][number];

export function withReminders(card: Card, added: ShownReminder[]): Card {
  const have = new Set(card.user.reminders.map((r) => r.id));
  return { ...card, user: { ...card.user, reminders: [...card.user.reminders, ...added.filter((r) => !have.has(r.id))] } };
}

export function withoutReminder(card: Card, id: string): Card {
  return { ...card, user: { ...card.user, reminders: card.user.reminders.filter((r) => r.id !== id) } };
}

/** The two rows "Recommended reminders" will create, as the card will show them until the server's real ids arrive. */
export function pendingRecommended(card: Card, baseId: string): ShownReminder[] {
  return (card.recommended_reminders ?? [])
    .filter((r) => r.fire_at)
    .map((r) => ({ id: r.kind === 'recommended_day_before' ? baseId : `${baseId}:pending`, kind: r.kind, fire_at: r.fire_at, virtual: false }));
}

export const recommendedAlreadySet = (card: Card): boolean => card.user.reminders.some((r) => r.kind.startsWith('recommended_'));
/** A reminder that exists only on this phone so far (its real id has not come back from the server). */
export const isPending = (r: ShownReminder): boolean => r.id.endsWith(':pending');
