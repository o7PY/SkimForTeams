import { ApiError } from './api';

/** What a student types: any case, spaces or dashes in between. The server also ignores case and spaces. */
export function normalizeCode(input: string): string {
  return input.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 12);
}

export const MIN_CODE_LENGTH = 4;
export const canSubmit = (code: string) => normalizeCode(code).length >= MIN_CODE_LENGTH;

export type TeamInfo = { id: string; name: string | null; joined_at: string; muted: boolean; ended: boolean };

/** The friendly sentence for a failed join (PHASE4.md section 7.2). Never includes the code the student typed. */
export function joinErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.isNetwork) return 'No connection. Check your internet and try again.';
    if (e.status === 404) return 'No team has that code. Check it with your professor.';
    if (e.status === 410) return 'The bot was removed from that team, so it can no longer be joined.';
    if (e.status === 429) return 'Too many tries. Please wait a while before trying again.';
    if (e.status === 400) return 'Please type the code your professor shared.';
    if (e.status === 401) return e.message;
  }
  return 'Something went wrong. Please try again.';
}

/** A join that answers 409 means "you are already in": that is a success, and the team comes back in the body. */
export function alreadyJoinedTeam(e: unknown): TeamInfo | null {
  if (e instanceof ApiError && e.status === 409) {
    const t = (e.body as { team?: TeamInfo } | null)?.team;
    return t && typeof t.id === 'string' ? t : null;
  }
  return null;
}
