// "Ask the course" (PHASE6A): the request, the shapes, and the small rules the chat sheet follows. The conversation lives in memory
// only: nothing here is saved on the phone, and the server stores none of it either.
import { useCallback } from 'react';

import { safeUrl } from './actions';
import { ApiError } from './api';
import { useAuth } from './auth';
import { useMe } from './queries';

export type ChatCitation = {
  n: number;
  item_id?: number;
  message_id?: string;
  document_id?: number;
  link?: string; // a Teams link or a file link, when the source has no card (P6A-DIFF-001)
  team: string;
  title: string;
  snippet: string;
};
export type ChatReply = { answer: string; citations: ChatCitation[]; refused: boolean };
export type ChatTurn = { id: string; role: 'user' | 'assistant'; text: string; citations?: ChatCitation[]; refused?: boolean; notice?: boolean };
export type HistoryTurn = { role: 'user' | 'assistant'; text: string };

export const MAX_QUESTION = 500;
export const HISTORY_TURNS = 6;

export const ANCHORED_STARTERS = ['When is this?', 'Has this changed?', 'What do I need to submit?'];
export const OPEN_STARTERS = ['What’s due this week?', 'When’s the next quiz?'];
export const suggestions = (anchored: boolean): string[] => (anchored ? ANCHORED_STARTERS : OPEN_STARTERS);

/** What the server is sent as history: the last turns, in order, without the app's own notices (errors) in between. */
export function toHistory(turns: readonly ChatTurn[]): HistoryTurn[] {
  return turns.filter((t) => !t.notice && t.text.trim()).slice(-HISTORY_TURNS).map((t) => ({ role: t.role, text: t.text }));
}

export type CitationAction = { kind: 'card'; itemId: number } | { kind: 'link'; url: string } | { kind: 'none' };

/** What tapping a citation chip does: open its card; else open it in Teams (or the file); else nothing (the chip is just a label). */
export function citationAction(c: Pick<ChatCitation, 'item_id' | 'link'>): CitationAction {
  if (Number.isInteger(c.item_id) && (c.item_id as number) > 0) return { kind: 'card', itemId: c.item_id as number };
  const url = safeUrl(c.link);
  return url ? { kind: 'link', url } : { kind: 'none' };
}

/** A friendly sentence for every way asking can fail. */
export function chatErrorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return 'Something went wrong. Please try again.';
  if (e.status === 0) return /timed out/i.test(e.message) ? 'That took too long. Please try again.' : 'No connection. Check your internet and try again.';
  if (e.status === 429) return e.body?.error ?? 'You’ve asked a lot of questions. Please try again in a little while.';
  if (e.status === 503) return 'The course assistant isn’t available right now.';
  if (e.status === 502 || e.status === 504) return e.body?.error ?? 'The course assistant is busy. Please try again in a minute.';
  if (e.status === 400) return 'That question couldn’t be sent. Try asking it differently.';
  if (e.status === 401) return 'Your session has ended. Please sign in again.';
  return 'Something went wrong. Please try again.';
}

/** The chat is on only when the server says so (`/me` features.chat); until it is known it stays hidden. */
export function useChatEnabled(): boolean {
  return useMe().data?.features?.chat === true;
}

export function useAskCourse() {
  const { api } = useAuth();
  return useCallback(
    (req: { question: string; itemId: number | null; history: HistoryTurn[] }) =>
      api.post<ChatReply>('/chat', { question: req.question, ...(req.itemId !== null ? { item_id: req.itemId } : {}), history: req.history }),
    [api],
  );
}
