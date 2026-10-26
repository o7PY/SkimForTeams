import type { Card, EventChip } from '@/lib/types';

export const event = (over: Partial<EventChip> = {}): EventChip => ({
  id: 1, title: 'Quiz 1', type: 'quiz', start: '2026-11-02T10:00:00+05:30', end: null, all_day: false, venue: 'LHC 104',
  status: 'scheduled', unconfirmed: false, important: true, ...over,
});

const base = (): Card => ({
  id: 1, kind: 'message', team: { id: 'T1', name: 'MA 221', ended: false }, occurred_at: '2026-10-25T18:00:00+05:30', folded_ids: [],
  author: { name: 'Prof Rao', is_staff: true }, message: null, document: null, change: null, events: [],
  links: { teams_deep_link: null, file_url: null }, user: { starred: false, temp_star_expires_at: null, reminders: [], snoozed_until: null },
});

export const messageCard = (over: Partial<Card> = {}): Card => ({
  ...base(),
  message: { subject: null, text: 'Quiz 1 is on Monday\nBring a calculator and your ID card.', text_truncated: false, edited: false, deleted: false, reply_to_subject: null, attachments: [] },
  ...over,
});

export const documentCard = (over: Partial<Card> = {}): Card => ({
  ...base(), id: 2, kind: 'document',
  document: { name: 'Syllabus v2.pdf', action: 'renamed', old_name: 'Syllabus.pdf', old_path: null, web_url: 'https://sp/x.pdf' },
  ...over,
});

export const changeCard = (over: Partial<Card> = {}): Card => ({
  ...base(), id: 3, kind: 'change', author: null,
  change: { type: 'rescheduled', before: { start: '2026-10-30T10:00:00+05:30' }, after: { start: '2026-11-02T10:00:00+05:30' } },
  events: [event()], folded_ids: [8, 9],
  ...over,
});

export const cardsWithIds = (...ids: number[]): Card[] => ids.map((id) => messageCard({ id }));
