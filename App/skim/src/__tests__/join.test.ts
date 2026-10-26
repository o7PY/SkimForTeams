import { ApiError } from '@/lib/api';
import { alreadyJoinedTeam, canSubmit, joinErrorMessage, normalizeCode } from '@/lib/join';

test.each([
  ['r6bve4', 'R6BVE4'], [' r6 bve-4 ', 'R6BVE4'], ['R6BVE4\n', 'R6BVE4'], ['ab_cd.12', 'ABCD12'], ['', ''], ['x'.repeat(40), 'X'.repeat(12)],
])('normalizeCode(%j) = %j', (input, out) => expect(normalizeCode(input)).toBe(out));

test('the button needs at least 4 letters or digits', () => {
  expect(canSubmit('abc')).toBe(false);
  expect(canSubmit('a-b c')).toBe(false);
  expect(canSubmit('R6BVE4')).toBe(true);
});

test.each([
  [new ApiError(404, { error: 'no team has that code' }), 'No team has that code'],
  [new ApiError(410, null), 'bot was removed'],
  [new ApiError(429, null), 'Too many tries'],
  [new ApiError(0, null, 'No connection'), 'No connection'],
  [new ApiError(400, null), 'type the code'],
  [new ApiError(500, null), 'Something went wrong'],
  [new Error('x'), 'Something went wrong'],
])('error %# gets a friendly message', (e, contains) => expect(joinErrorMessage(e)).toContain(contains));

test('messages never echo what the student typed or what the server said', () => {
  const m = joinErrorMessage(new ApiError(404, { error: 'SECRET-SERVER-TEXT R6BVE4' }));
  expect(m).not.toContain('R6BVE4');
  expect(m).not.toContain('SECRET');
});

test('409 already joined is a success: the team is read from the body', () => {
  const team = { id: 'T1', name: 'MA 221', joined_at: 'x', muted: false, ended: false };
  expect(alreadyJoinedTeam(new ApiError(409, { error: 'already joined', team }))).toEqual(team);
  expect(alreadyJoinedTeam(new ApiError(409, { error: 'x' }))).toBeNull();
  expect(alreadyJoinedTeam(new ApiError(404, { team }))).toBeNull();
  expect(alreadyJoinedTeam(new Error('x'))).toBeNull();
});
