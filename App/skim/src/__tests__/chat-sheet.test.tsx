// The chat sheet: starters, anchoring, sending, answers with citation chips, history, errors, offline, and that closing clears it.
import { Linking } from 'react-native';
import { act, create, type ReactTestInstance } from 'react-test-renderer';

import { ApiError } from '@/lib/api';
import type { ChatReply } from '@/lib/chat';
import { ChatSheet } from '@/ui/chat-sheet';

const mockAsk = jest.fn();
const mockPush = jest.fn();
let mockOnline = true;

jest.mock('@react-native-async-storage/async-storage', () => jest.requireActual('@react-native-async-storage/async-storage/jest/async-storage-mock'));
jest.mock('@/lib/auth', () => ({ useAuth: () => ({ api: {} }) }));
jest.mock('@/lib/chat', () => ({ ...jest.requireActual('@/lib/chat'), useAskCourse: () => (req: unknown) => mockAsk(req) }));
jest.mock('@/lib/use-online', () => ({ useOnline: () => mockOnline }));
jest.mock('expo-router', () => ({ router: { push: (...a: unknown[]) => mockPush(...a) } }));
jest.mock('react-native-safe-area-context', () => ({ SafeAreaView: jest.requireActual('react-native').View }));
jest.mock('@/ui/icon', () => {
  const { Text } = jest.requireActual('react-native');
  return { Icon: ({ name }: { name: string }) => <Text>{`[icon:${name}]`}</Text> };
});

const ANCHOR = { id: 42, title: 'Quiz 1 on Monday' };
const isText = (n: ReactTestInstance) => (n.type as unknown) === 'Text';
const words = (r: ReturnType<typeof create>) => r.root.findAll(isText).map((n) => n.children.join('')).join(' | ');
const labelled = (r: ReturnType<typeof create>, label: string) => r.root.findAll((n) => n.props.accessibilityLabel === label && typeof n.props.onPress === 'function');
const press = (r: ReturnType<typeof create>, label: string) => act(() => labelled(r, label)[0].props.onPress());
const input = (r: ReturnType<typeof create>) => r.root.findAll((n) => n.props.accessibilityLabel === 'Your question' && typeof n.props.onChangeText === 'function')[0];
const type = (r: ReturnType<typeof create>, text: string) => act(() => input(r).props.onChangeText(text));
const reply = (over: Partial<ChatReply> = {}): ChatReply => ({ answer: 'The quiz is on Mon 2 Nov at 10:00 [1].', refused: false, citations: [{ n: 1, item_id: 42, team: 'MA 221', title: 'Quiz 1 on Monday', snippet: 'Quiz 1 will be…' }], ...over });
const render = (visible = true, anchor: typeof ANCHOR | null = ANCHOR, onClose = jest.fn()) => {
  let r!: ReturnType<typeof create>;
  act(() => { r = create(<ChatSheet visible={visible} anchor={anchor} onClose={onClose} />); });
  return { r, onClose, set: (v: boolean) => act(() => r.update(<ChatSheet visible={v} anchor={anchor} onClose={onClose} />)) };
};
const flush = () => act(async () => { await Promise.resolve(); });

beforeEach(() => {
  jest.clearAllMocks();
  mockOnline = true;
  mockAsk.mockResolvedValue(reply());
  (global as { __REDUCED_MOTION__?: boolean }).__REDUCED_MOTION__ = false;
});

test('closed: nothing is on the screen', () => {
  expect(render(false).r.toJSON()).toBeNull();
});

test('anchored: the card chip and the three starter questions', () => {
  const { r } = render();
  const t = words(r);
  expect(t).toContain('Ask the course');
  expect(t).toContain('About: Quiz 1 on Monday');
  for (const s of ['When is this?', 'Has this changed?', 'What do I need to submit?']) expect(t).toContain(s);
});

test('not anchored: no chip, and the two open starter questions', () => {
  const { r } = render(true, null);
  const t = words(r);
  expect(t).not.toContain('About:');
  expect(t).toContain('What’s due this week?');
  expect(t).toContain('When’s the next quiz?');
  expect(t).not.toContain('When is this?');
});

test('a starter sends that question about the anchored card, and the answer shows with its source chip', async () => {
  const { r } = render();
  press(r, 'When is this?');
  await flush();
  expect(mockAsk).toHaveBeenCalledWith({ question: 'When is this?', itemId: 42, history: [] });
  const t = words(r);
  expect(t).toContain('When is this?');                       // the student's own bubble
  expect(t).toContain('The quiz is on Mon 2 Nov at 10:00 [1].');
  expect(t).toContain('[1] Quiz 1 on Monday');                 // the citation chip
  expect(t).not.toContain('Thinking…');                        // done
});

test('while waiting there is one "Thinking…" bubble and sending is blocked', async () => {
  let release!: (v: ChatReply) => void;
  mockAsk.mockReturnValue(new Promise<ChatReply>((res) => { release = res; }));
  const { r } = render();
  type(r, 'when is it');
  press(r, 'Send');
  await flush();
  expect(words(r)).toContain('Thinking…');
  expect(input(r).props.editable).toBe(false);
  press(r, 'Send');                                            // a second tap while busy does nothing
  expect(mockAsk).toHaveBeenCalledTimes(1);
  await act(async () => { release(reply()); await Promise.resolve(); });
  expect(words(r)).not.toContain('Thinking…');
});

test('the next question carries the last turns as history, and an error notice is not part of it', async () => {
  const { r } = render();
  press(r, 'When is this?');
  await flush();
  mockAsk.mockRejectedValueOnce(new ApiError(502, { error: 'The course assistant is busy right now. Please try again in a minute.' }));
  type(r, 'and the venue?');
  press(r, 'Send');
  await flush();
  expect(words(r)).toContain('The course assistant is busy right now.');
  type(r, 'thanks, and the time?');
  press(r, 'Send');
  await flush();
  const third = mockAsk.mock.calls[2][0];
  expect(third.question).toBe('thanks, and the time?');
  expect(third.history.map((h: { role: string }) => h.role)).toEqual(['user', 'assistant', 'user']);   // the busy notice is left out
  expect(JSON.stringify(third.history)).not.toContain('busy');
});

test('the ✕ on the chip stops asking about the card: later questions are unanchored', async () => {
  const { r } = render();
  press(r, 'Stop asking about this card');
  expect(words(r)).not.toContain('About:');
  expect(words(r)).toContain('What’s due this week?');         // the open starters appear
  press(r, 'What’s due this week?');
  await flush();
  expect(mockAsk).toHaveBeenCalledWith({ question: 'What’s due this week?', itemId: null, history: [] });
});

describe('citation chips', () => {
  test('a source with a card closes the sheet and opens the card', async () => {
    const { r, onClose } = render();
    press(r, 'When is this?');
    await flush();
    act(() => labelled(r, 'Source 1: Quiz 1 on Monday, MA 221. Open the card')[0].props.onPress());
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/item/[id]', params: { id: '42' } });
  });
  test('a source without a card opens in Teams', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
    mockAsk.mockResolvedValue(reply({ citations: [{ n: 1, message_id: 'm9', link: 'https://teams.cloud.microsoft/l/message/x', team: 'MA 221', title: 'Old post', snippet: '…' }] }));
    const { r, onClose } = render();
    press(r, 'When is this?');
    await flush();
    act(() => labelled(r, 'Source 1: Old post, MA 221. Open in Teams')[0].props.onPress());
    expect(open).toHaveBeenCalledWith('https://teams.cloud.microsoft/l/message/x');
    expect(onClose).not.toHaveBeenCalled();
    open.mockRestore();
  });
  test('a source with neither is just a label, not a button', async () => {
    mockAsk.mockResolvedValue(reply({ citations: [{ n: 2, team: 'MA 221', title: 'A file', snippet: '…' }] }));
    const { r } = render();
    press(r, 'When is this?');
    await flush();
    const chip = r.root.findAll((n) => n.props.accessibilityLabel === 'Source 2: A file, MA 221')[0];
    expect(chip.props.disabled).toBe(true);
    expect(chip.props.accessibilityRole).toBe('text');
  });
});

describe('friendly failures', () => {
  test.each([
    [new ApiError(429, { error: 'You’ve asked a lot of questions this hour. Please try again in a little while.' }), 'this hour'],
    [new ApiError(503, { error: 'x' }), 'isn’t available right now'],
    [new ApiError(0, null, 'The request timed out'), 'took too long'],
    [new ApiError(0, null, 'No connection'), 'No connection'],
  ])('%#', async (err, expected) => {
    mockAsk.mockRejectedValue(err);
    const { r } = render();
    press(r, 'When is this?');
    await flush();
    expect(words(r)).toContain(expected);
    expect(words(r)).toContain('[icon:alert-circle-outline]');
  });
  test('a refusal reads as a normal answer with no chips', async () => {
    mockAsk.mockResolvedValue(reply({ answer: 'I couldn’t find that in your course messages.', refused: true, citations: [] }));
    const { r } = render();
    press(r, 'When is this?');
    await flush();
    expect(words(r)).toContain('I couldn’t find that in your course messages.');
    expect(words(r)).not.toContain('[1]');
  });
});

test('offline: the banner shows, the field and the starters are disabled and nothing is sent', async () => {
  mockOnline = false;
  const { r } = render();
  expect(words(r)).toContain('You’re offline');
  expect(input(r).props.editable).toBe(false);
  expect(input(r).props.placeholder).toBe('You’re offline');
  expect(labelled(r, 'Send')[0].props.accessibilityState.disabled).toBe(true);
  press(r, 'When is this?');
  await flush();
  expect(mockAsk).not.toHaveBeenCalled();
});

test('an empty or blank question is never sent, and a long one is cut to 500 characters', async () => {
  const { r } = render();
  type(r, '   ');
  expect(labelled(r, 'Send')[0].props.accessibilityState.disabled).toBe(true);
  expect(input(r).props.maxLength).toBe(500);
  type(r, 'x'.repeat(600));
  press(r, 'Send');
  await flush();
  expect(mockAsk.mock.calls[0][0].question).toHaveLength(500);
});

test('closing clears the conversation: opening again starts empty', async () => {
  const { r, set } = render();
  press(r, 'When is this?');
  await flush();
  expect(words(r)).toContain('The quiz is on Mon 2 Nov at 10:00 [1].');
  set(false);
  expect(r.toJSON()).toBeNull();                               // slid away and unmounted
  set(true);
  expect(words(r)).not.toContain('The quiz is on Mon 2 Nov');
  expect(words(r)).toContain('When is this?');                 // the starters are back
  press(r, 'When is this?');
  await flush();
  expect(mockAsk.mock.calls[1][0].history).toEqual([]);        // and nothing from before is sent
});

test('the close buttons close it, and a screen reader can reach every control by its label', () => {
  const { r, onClose } = render();
  const closes = labelled(r, 'Close');
  expect(closes.length).toBeGreaterThan(0);
  act(() => closes[0].props.onPress());
  expect(onClose).toHaveBeenCalled();
  for (const label of ['Your question', 'Send', 'Stop asking about this card']) {
    expect(r.root.findAll((n) => n.props.accessibilityLabel === label).length).toBeGreaterThan(0);
  }
});

test('reduced motion: the sheet fades (opacity) instead of sliding (transform)', () => {
  (global as { __REDUCED_MOTION__?: boolean }).__REDUCED_MOTION__ = true;
  const { r } = render();
  const styles = r.root.findAll((n) => n.props.style && typeof n.props.style === 'object').map((n) => JSON.stringify(n.props.style));
  expect(styles.some((s) => s.includes('"opacity"'))).toBe(true);
  expect(styles.some((s) => s.includes('translateY'))).toBe(false);
});
